"""The HTTP API surface: primitives and assured operations over FastAPI.

A thin, typed veneer: every endpoint delegates to one QueryEngine primitive or
runs one operation through the shared ``OperationSurface``. ``/operations``
is the registry's active rows, so CLI and MCP stay in lockstep by construction.

The API is the one place authorization is enforced for query-engine reads
(retrieval §9): a deployment that passes an `AuthPerimeterPort` gets every
endpoint gated on a valid perimeter credential for THIS deployment — a single
trust domain. With no port, the perimeter is infrastructure's job and the app
is open (the self-host default). The surface itself never touches adapters.

Every route reads its deployment and capabilities from one FastAPI dependency,
:func:`deployment_context`, which by default returns the deployment `build_api`
was composed with. A host that serves several deployments from one process
overrides that dependency and returns the request's deployment (D147); each
request is still served by exactly one deployment.
"""

from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
import functools
import inspect
import json
import logging
import re
from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal
from typing import Protocol
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Body
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPBearer
from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import SecretBytes
from pydantic import ValidationError
from sqlalchemy.exc import InternalError
from sqlalchemy.exc import OperationalError
from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from remember.mcp_tools import ADJACENT_CHUNKS_TOOL_NAME
from remember.mcp_tools import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools import DOCUMENT_REFERENCES_TOOL_NAME
from remember.mcp_tools import INGEST_TOOL_NAME
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import OPERATION_TOOL_NAMES
from remember.mcp_tools import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools import SEARCH_DOCUMENTS_TOOL_NAME
from remember.mcp_tools import SECTION_HISTORY_TOOL_NAME
from remember.mcp_tools import TAG_READ_TOOL_NAMES
from remember.mcp_tools import TAG_WRITE_TOOL_NAMES
from remember.mcp_tools import tool
from remember.models import DocumentReferencesPage
from remember.models import DocumentReferencesRequest
from remember.models import DocumentTags
from remember.models import DocumentTagsUpdate
from remember.models import EffectivePeriodInput
from remember.models import EffectivePeriodsRequest
from remember.models import EffectivePeriodsSet
from remember.models import EffectiveTimeCleared
from remember.models import ReferenceGenerations
from remember.models import REFERENCES_BODY_MAX_BYTES
from remember.models import ReferencesSet
from remember.models import Tag
from remember.models import TAG_REF_MAX_RAW_LEN
from remember.models import TagCreate
from remember.models import TagList
from remember.models import TagRef
from remember.models import TAGS_PER_DOCUMENT_MAX
from remember.models import TagUpdate
from remember.models import VERSION_KEY_MAX_LEN
from rememberstack import __version__
from rememberstack.model import ADJACENT_CHUNKS_MAX_WINDOW
from rememberstack.model import ADJACENT_CHUNKS_MIN_WINDOW
from rememberstack.model import AdjacentChunksRequest
from rememberstack.model import AuthenticatedContext
from rememberstack.model import ChunkNotFoundError
from rememberstack.model import ConnectorCreate
from rememberstack.model import ConnectorDescriptor
from rememberstack.model import ConnectorNotFoundError
from rememberstack.model import ContentDetectionError
from rememberstack.model import ContextBundleV2
from rememberstack.model import DeploymentBuildInfo
from rememberstack.model import DocumentDeletion
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentPage
from rememberstack.model import DocumentStatusFilter
from rememberstack.model import DocumentUpload
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EffectivePeriodConflictError
from rememberstack.model import EffectiveTimeNotSupportedError
from rememberstack.model import Envelope
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ForgottenSourceError
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import IngestPrincipalKind
from rememberstack.model import PerimeterCredential
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ProviderCallError
from rememberstack.model import ReadEmbeddingCost
from rememberstack.model import ReadinessRequirements
from rememberstack.model import ReferenceBodyError
from rememberstack.model import SearchRequest
from rememberstack.model import SpendLeaseRefused
from rememberstack.model import SpendLeaseUnavailable
from rememberstack.model import TagConflictError
from rememberstack.model import TagLimitError
from rememberstack.model import ToolDescriptor
from rememberstack.model import track_read_embedding_cost
from rememberstack.model import UnknownTagError
from rememberstack.model import VersionKeyConflictError
from rememberstack.model.auth import PerimeterScope
from rememberstack.model.client import DocumentSearchPage
from rememberstack.model.client import DocumentSearchRequest
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.client import ProcessingLayerStates
from rememberstack.model.client import SECTION_HISTORY_DEFAULT_LIMIT
from rememberstack.model.client import SECTION_HISTORY_MAX_LIMIT
from rememberstack.model.client import SectionHistoryPage
from rememberstack.model.client import SectionHistoryRequest
from rememberstack.model.processing_layers import LayerAdmission
from rememberstack.model.processing_layers import LayerSelectionError
from rememberstack.model.processing_layers import LayerUnavailableError
from rememberstack.ports.auth import AuthPerimeterPort
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.spine.new_work import announce_new_work
from rememberstack.spine.new_work import collect_new_work
from rememberstack.surfaces.direct_admission import admission_key
from rememberstack.surfaces.direct_admission import AdmissionRefused
from rememberstack.surfaces.direct_admission import DirectPathAdmission
from rememberstack.surfaces.direct_admission import RequestHold
from rememberstack.surfaces.graph_queries import GraphBusyError
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.operation_surface import InvalidArgumentError
from rememberstack.surfaces.operation_surface import MissingArgumentError
from rememberstack.surfaces.operation_surface import OperationSurface
from rememberstack.surfaces.operation_surface import ProcessingLayersPort
from rememberstack.surfaces.operation_surface import UnknownOperationError
from rememberstack.surfaces.operation_surface import unserved_operation_names
from rememberstack.surfaces.query_engine import disclose_layers
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_engine import RESOLVE_CONTEXT_LIMIT
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.route_scope import operation_scope
from rememberstack.surfaces.route_scope import required_scope

PIPELINE_READINESS_VERSION_LIMIT: Final = 1_000
"""Maximum document versions in one read-only readiness inspection."""

GraphPredicate = Annotated[str, Field(min_length=1, max_length=200)]

SavedQueryStatus = Literal[
    "draft", "pending_revalidation", "active", "deprecated", "disabled", "broken"
]
"""The saved-query version states `GET /query/saved?status=` may filter on."""

LOOKUP_K_MAX: Final = 400
LOOKUP_BROWSE_LIMIT_DEFAULT: Final = 50
LOOKUP_BROWSE_LIMIT_MAX: Final = 200
"""Largest `k` a fact lookup returns in one call; more is reported as truncated."""


def _require_utc(value: datetime) -> datetime:
    """Refuse naive and non-UTC instants at the boundary (422, never 500)."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("must be timezone-aware UTC (end it with Z)")
    return value


UTCInstant = Annotated[datetime, AfterValidator(_require_utc)]
"""A request instant: ISO 8601 with a zero UTC offset."""


class IngestPort(Protocol):
    """The E0 ingest operations the HTTP surface may expose."""

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion: ...

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion: ...


class SpendLeasePort(Protocol):
    """Metadata-only spend hold used by managed self-host (D46)."""

    def reserve(
        self,
        *,
        authorization: str,
        path_id: str,
        content_length: int | None = None,
        mime: str | None = None,
        operation_name: str | None = None,
    ) -> UUID: ...

    def commit(
        self,
        *,
        authorization: str,
        reservation_id: UUID,
        read_cost: ReadEmbeddingCost | None,
    ) -> None:
        """Commit the hold; a read reports its embedding cost, ingest ``None``."""
        ...

    def release(self, *, authorization: str, reservation_id: UUID) -> None: ...


class ConnectorManagementPort(Protocol):
    """Manage deployment-side connector configuration, never run it client-side."""

    def connectors(self, *, deployment_id: UUID) -> tuple[ConnectorDescriptor, ...]: ...

    def add(
        self, *, deployment_id: UUID, connector: ConnectorCreate
    ) -> ConnectorDescriptor: ...

    def pause(
        self, *, deployment_id: UUID, connector_id: UUID
    ) -> ConnectorDescriptor: ...

    def status(
        self, *, deployment_id: UUID, connector_id: UUID
    ) -> ConnectorDescriptor: ...


class AdmissionPort(Protocol):
    """The deployment-wide fail-closed check applied before public traffic."""

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Raise ``ForgetInProgressError`` while D74 admission is closed."""
        ...


class ReadinessPort(Protocol):
    """The mandatory restore replay completed before an API begins serving."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Re-honor every portable forget manifest or raise fail-closed."""
        ...


class PipelineReadinessPort(Protocol):
    """Inspect requested pipeline and serving-capability readiness."""

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require: ReadinessRequirements,
    ) -> PipelineReadinessReport: ...


class DocumentSearchPort(Protocol):
    """Find documents by name, general metadata and content (D134)."""

    def search_documents(
        self, *, deployment_id: UUID, request: DocumentSearchRequest
    ) -> DocumentSearchPage: ...


class SectionHistoryPort(Protocol):
    """Follow one keyed section across a lineage's versions (D140 §6.2)."""

    def section_history(
        self, *, deployment_id: UUID, request: SectionHistoryRequest
    ) -> SectionHistoryPage:
        """Read one page, or raise ``DocumentNotFoundError`` / ``ValueError``."""
        ...


class ReferencesPort(Protocol):
    """Record and inspect one version's supplied references (D140 §6.3)."""

    def set_references(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID, body: bytes
    ) -> ReferencesSet:
        """Record an NDJSON set; ``ReferenceBodyError`` for an invalid line."""
        ...

    def reference_generations(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID
    ) -> ReferenceGenerations:
        """The version's generations with statuses and errors."""
        ...


class DocumentReferencesPort(Protocol):
    """Read references across versions and time (D140 §6.2)."""

    def document_references(
        self, *, deployment_id: UUID, request: DocumentReferencesRequest
    ) -> DocumentReferencesPage:
        """Read one page, or raise a not-found error or ``ValueError``."""
        ...


class DocumentInventoryPort(Protocol):
    """List the document lineages this deployment holds."""

    def list_documents(
        self,
        *,
        deployment_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
        status: DocumentStatusFilter | None = None,
    ) -> DocumentPage: ...


class DocumentDeletionPort(Protocol):
    """Remove one document's contribution to the live memory (D135)."""

    def delete_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentDeletion:
        """Delete the lineage, or raise ``DocumentNotFoundError`` when absent."""
        ...


class EffectiveTimePort(Protocol):
    """Declare when document versions are in force, after ingest (D140)."""

    def set_effective_periods(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        periods: tuple[EffectivePeriodInput, ...],
    ) -> EffectivePeriodsSet:
        """Replace one version's live declarations with ``periods``."""
        ...

    def clear_effective_time(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> EffectiveTimeCleared:
        """Retract every declaration of the lineage and leave effective time."""
        ...


class TagPort(Protocol):
    """The deployment's tag registry and document tag writes (D156)."""

    def list_tags(
        self, *, deployment_id: UUID, query: str | None = None, name: str | None = None
    ) -> TagList:
        """Every registered tag, optionally narrowed by a substring or exact name."""
        ...

    def create_tag(self, *, deployment_id: UUID, request: TagCreate) -> Tag:
        """Register one tag, or raise ``TagConflictError``."""
        ...

    def update_tag(self, *, deployment_id: UUID, tag: str, request: TagUpdate) -> Tag:
        """Rename or re-describe one tag, or raise ``UnknownTagError``."""
        ...

    def delete_tag(self, *, deployment_id: UUID, tag: str) -> Tag:
        """Remove one tag and all its assignments."""
        ...

    def update_document_tags(
        self, *, deployment_id: UUID, doc_id: UUID, request: DocumentTagsUpdate
    ) -> DocumentTags:
        """Change one live document's tags atomically."""
        ...


class BuildInfoPort(Protocol):
    """Report the code and model bindings currently serving."""

    def build_info(self, *, deployment_id: UUID) -> DeploymentBuildInfo:
        """Answer without touching submitted work, so a caller can check first."""
        ...


class GraphQueryPort(Protocol):
    """Typed bounded graph operations exposed by the data plane."""

    def neighborhood(
        self,
        *,
        entity_id: UUID,
        hops: int = 2,
        predicates: tuple[str, ...] = (),
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
        limit: int = 100,
        cursor: str | None = None,
        include_paths: bool = False,
    ) -> Envelope: ...

    def path(
        self,
        *,
        from_entity_id: UUID,
        to_entity_id: UUID,
        max_hops: int = 4,
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
        predicates: tuple[str, ...] = (),
    ) -> Envelope: ...

    def citation_path(
        self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int = 6
    ) -> Envelope: ...


class PipelineReadinessRequest(BaseModel):
    """Exhaustive readiness capabilities for a bounded version set."""

    model_config = ConfigDict(extra="forbid")

    version_ids: list[UUID] = Field(
        min_length=1, max_length=PIPELINE_READINESS_VERSION_LIMIT
    )
    require: ReadinessRequirements


class GraphNeighborhoodRequest(BaseModel):
    """Bounded entity-neighborhood request."""

    model_config = ConfigDict(extra="forbid")

    entity_id: UUID
    hops: int = Field(default=2, ge=1, le=4)
    predicates: tuple[GraphPredicate, ...] = Field(default=(), max_length=100)
    valid_at: UTCInstant | None = None
    believed_at: UTCInstant | None = None
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)
    include_paths: bool = False

    @model_validator(mode="after")
    def require_complete_bitemporal_coordinate(self) -> Self:
        """Require both graph clocks or neither at the HTTP boundary."""
        if (self.valid_at is None) != (self.believed_at is None):
            raise ValueError("valid_at and believed_at must be supplied together")
        return self


class GraphPathRequest(BaseModel):
    """Bounded shortest entity-path request."""

    model_config = ConfigDict(extra="forbid")

    from_entity_id: UUID
    to_entity_id: UUID
    max_hops: int = Field(default=4, ge=1, le=6)
    predicates: tuple[GraphPredicate, ...] = Field(default=(), max_length=100)
    valid_at: UTCInstant | None = None
    believed_at: UTCInstant | None = None

    @model_validator(mode="after")
    def require_complete_bitemporal_coordinate(self) -> Self:
        """Require both graph clocks or neither at the HTTP boundary."""
        if (self.valid_at is None) != (self.believed_at is None):
            raise ValueError("valid_at and believed_at must be supplied together")
        return self


class GraphCitationPathRequest(BaseModel):
    """Bounded directed document-citation path request."""

    model_config = ConfigDict(extra="forbid")

    from_doc_id: UUID
    to_doc_id: UUID
    max_hops: int = Field(default=6, ge=1, le=6)


class SqlQueryRequest(BaseModel):
    """Body for `POST /query/sql` (execution fields allowed)."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    parameters: list[Any] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=0, le=1000)


class SqlExplainRequest(BaseModel):
    """Body for `POST /query/sql/explain` — sql and parameters only."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    parameters: list[Any] = Field(default_factory=list)


class RunSavedQueryRequest(BaseModel):
    """Body for `POST /query/saved/{namespace}/{name}/run`."""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)
    parameters: list[Any] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=0, le=1000)


@dataclass(frozen=True, kw_only=True)
class ApiDeployment:
    """The deployment a request is served from: its id and capabilities (D147).

    `build_api` mounts the routes for the capabilities its own deployment
    has, and every route reads them from the request's deployment through
    :func:`deployment_context`, never from values captured when the app was
    built. A capability absent from the request's deployment answers 404,
    as an unmounted route would. Its perimeter (``auth``, ``direct_admission``,
    ``spend_lease``) is applied per request too; only the startup readiness
    check stays composed per app.
    """

    deployment_id: UUID
    engine: QueryEngine
    admission: AdmissionPort
    surface: OperationSurface | None = None
    open_query: OpenQueryFacade | None = None
    ingest: IngestPort | None = None
    connectors: ConnectorManagementPort | None = None
    pipeline_readiness: PipelineReadinessPort | None = None
    documents: DocumentInventoryPort | None = None
    document_search: DocumentSearchPort | None = None
    section_history: SectionHistoryPort | None = None
    references: ReferencesPort | None = None
    document_references: DocumentReferencesPort | None = None
    deletion: DocumentDeletionPort | None = None
    effective_time: EffectiveTimePort | None = None
    tags: TagPort | None = None
    graph: GraphQueryPort | None = None
    build_info: BuildInfoPort | None = None
    processing_layers: ProcessingLayersPort | None = None
    #: When set, work rows a request creates are announced through it after
    #: the route returns (D147); unset, the database's insert trigger wakes
    #: listening workers, as in the self-host shell.
    task_queue: TaskQueuePort | None = None
    #: The perimeter credential check: a credential must be valid for this
    #: deployment. ``None``: no perimeter (infrastructure's job).
    auth: AuthPerimeterPort | None = None
    #: Per-credential and per-deployment rate and in-flight limits (D136 §7.6).
    direct_admission: DirectPathAdmission | None = None
    #: The D46 spend hold around memory reads, ingest and operations.
    spend_lease: SpendLeasePort | None = None

    def __post_init__(self) -> None:
        """One deployment is one trust domain (D50): bound parts must agree."""
        for name, part in (
            ("operation surface", self.surface),
            ("open-query facade", self.open_query),
        ):
            if part is not None and part.deployment_id != self.deployment_id:
                raise ValueError(
                    f"the {name} and the API serve different deployments —"
                    " one deployment is one trust domain (D50)"
                )


def deployment_context(request: Request) -> ApiDeployment:
    """The deployment this request is served from (D147).

    The default is the deployment `build_api` was composed with. A host that
    serves several deployments replaces it per app with
    ``app.dependency_overrides[deployment_context] = resolve``. The perimeter
    gate calls ``resolve(request)`` (a plain or ``async`` function of the
    request alone; the body is not read yet) once per request, before
    authentication, and every route, the D74 barrier and the perimeter use
    the result. ``resolve`` may raise ``HTTPException`` to refuse, for
    example 401 for a credential it cannot place. On its first request the
    gate takes ``resolve`` over (keeping it as the app's resolver) and points
    the override back at the resolved value, so FastAPI never calls
    ``resolve`` a second time: as a dependency anywhere, including a host's
    own routes and included routers, ``deployment_context`` returns the
    deployment the gate resolved and authenticated for the request.
    """
    return _request_deployment(request)


async def _resolve_deployment(scope: Scope) -> ApiDeployment:
    """Call the app's resolver (its ``deployment_context`` override) once."""
    app = scope["app"]
    overrides = app.dependency_overrides
    registered = overrides.get(deployment_context)
    if registered is None:
        return app.state.api_deployment
    if registered is not _request_deployment:
        app.state.deployment_resolver = registered
        overrides[deployment_context] = _request_deployment
    resolve = app.state.deployment_resolver
    request = Request(scope)
    if inspect.iscoroutinefunction(resolve):
        return await resolve(request)
    return await run_in_threadpool(resolve, request)


def _request_deployment(request: Request) -> ApiDeployment:
    """The deployment the gate resolved for this request (or the app's own)."""
    return getattr(request.state, "api_deployment", request.app.state.api_deployment)


_Deployment = Annotated[ApiDeployment, Depends(_request_deployment)]


def _served[T](capability: T | None) -> T:
    """The request's deployment capability, or 404 when it does not serve it."""
    if capability is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return capability


logger = logging.getLogger(__name__)


def build_api(
    *,
    engine: QueryEngine,
    deployment_id: UUID,
    admission: AdmissionPort,
    readiness: ReadinessPort,
    surface: OperationSurface | None = None,
    open_query: OpenQueryFacade | None = None,
    auth: AuthPerimeterPort | None = None,
    direct_admission: DirectPathAdmission | None = None,
    spend_lease: SpendLeasePort | None = None,
    ingest: IngestPort | None = None,
    connectors: ConnectorManagementPort | None = None,
    pipeline_readiness: PipelineReadinessPort | None = None,
    documents: DocumentInventoryPort | None = None,
    document_search: DocumentSearchPort | None = None,
    section_history: SectionHistoryPort | None = None,
    references: ReferencesPort | None = None,
    document_references: DocumentReferencesPort | None = None,
    deletion: DocumentDeletionPort | None = None,
    effective_time: EffectiveTimePort | None = None,
    tags: TagPort | None = None,
    graph: GraphQueryPort | None = None,
    build_info: BuildInfoPort | None = None,
    processing_layers: ProcessingLayersPort | None = None,
    task_queue: TaskQueuePort | None = None,
    ingest_scope_routes: Collection[tuple[str, str]] = (),
    ingest_body_max_bytes: int | None = None,
    trusted_principal_source: bool = False,
    browser_origins: tuple[str, ...] = (),
) -> FastAPI:
    """Build one deployment's query API over a composed engine.

    `surface` adds registry-rendered operations; `open_query` adds the §3.1 open
    query routes; `ingest` exposes the E0 write gate; `connectors` manages
    deployment-side connector configuration; `deletion` adds
    `DELETE /documents/{doc_id}` (D135); `effective_time` adds
    `PUT /documents/{doc_id}/versions/{version_id}/effective-periods` and
    `DELETE /documents/{doc_id}/effective-periods` (D140); `tags` adds
    `GET|POST /tags`, `PATCH|DELETE /tags/{tag_id}` and
    `POST /documents/{doc_id}/tags` (D156); `document_search` adds
    `POST /documents/search` (D134); `section_history` adds
    `GET /documents/{doc_id}/sections/{section_key}/history` (D140);
    `references` adds `PUT|GET /documents/{doc_id}/versions/{version_id}/references`
    and `document_references` adds `POST /documents/references` (D140);
    `auth` gates every request
    on one perimeter credential; `direct_admission` enforces the per-credential
    and per-deployment rate and in-flight limits after authentication and
    before the spend lease and routing (D136 §7.6); and `spend_lease` holds
    estimate on the
    control plane for ingest/search/operations POST (D46). Each capability
    is explicitly composed; absent services do not pretend to exist.

    `trusted_principal_source` declares that this deployment's network perimeter
    is reached only by callers entitled to state who ingested a document. It is
    **off by default**, and a configured auth perimeter additionally requires
    full WRITE authority before believing the headers. The unscoped shared
    secret is unrestricted; a narrow signed browser ingest credential may add
    a document, but its attribution headers are ignored and its own subject
    never becomes attribution. Elsewhere an asserted `X-Ingest-Principal-*`
    pair is **ignored, never rejected** — metadata must not be able to fail an
    otherwise valid ingest.

    `ingest_scope_routes` names a host's own routes mounted in this app, as
    ``(METHOD, "/path/{param}")`` exactly as the route is declared, that a
    narrow ingest credential may reach as it may reach `POST /ingest` (D147).
    A grant applies to the declared route only, never to a route the scope
    table already classifies; every other host route needs WRITE.

    `ingest_body_max_bytes` bounds `POST /ingest` request bodies before they
    are buffered (413 over the cap; 411 when no Content-Length is declared).
    None — the self-host default — imposes no limit: caps are deployment
    policy, never an engine default (D61).
    """
    deployment = ApiDeployment(
        deployment_id=deployment_id,
        engine=engine,
        admission=admission,
        surface=surface,
        open_query=open_query,
        ingest=ingest,
        connectors=connectors,
        pipeline_readiness=pipeline_readiness,
        documents=documents,
        document_search=document_search,
        section_history=section_history,
        references=references,
        document_references=document_references,
        deletion=deletion,
        effective_time=effective_time,
        tags=tags,
        graph=graph,
        build_info=build_info,
        processing_layers=processing_layers,
        task_queue=task_queue,
        auth=auth,
        direct_admission=direct_admission,
        spend_lease=spend_lease,
    )
    readiness.ensure_ready(deployment_id=deployment_id)
    dependencies = [
        # The route's scope check; the gate has authenticated already. One
        # instance, so open-query principal injection reuses its result.
        Depends(_perimeter),
        # Declares the bearer scheme in the OpenAPI document so a generated
        # client knows to send a credential. `auto_error=False` means it never
        # raises: the gate and `_perimeter` stay the enforcement points and
        # keep answering 401, so documenting the contract cannot change it.
        *([Depends(HTTPBearer(auto_error=False))] if auth is not None else []),
        Depends(_admission),
        Depends(_announce_new_work),
    ]
    app = FastAPI(
        title="RememberStack query API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # a machine API; the schema endpoint is not gated, so off
        dependencies=dependencies,
    )
    app.state.api_deployment = deployment
    app.state.ingest_scope_routes = frozenset(
        (method.upper(), path) for method, path in ingest_scope_routes
    )
    # Before any route is declared, so every one is built held: a request's
    # deployment may bring admission limits even when this one has none. A
    # held callable without a hold runs unchanged.
    app.router.route_class = _HeldRoute

    @app.exception_handler(ProviderCallError)
    def model_provider_unavailable(
        _request: Request, _error: Exception
    ) -> JSONResponse:
        """Any route whose embedding call fails answers 503, never 500."""
        return JSONResponse(
            status_code=503, content={"detail": "model provider unavailable"}
        )

    @app.exception_handler(LayerUnavailableError)
    def layer_unavailable(_request: Request, error: Exception) -> JSONResponse:
        """D143 §7.2: a read needs a processing layer this deployment does not serve."""
        assert isinstance(error, LayerUnavailableError)
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "layer_unavailable",
                    "layer": error.layer.value,
                    "status": "disabled",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(LayerSelectionError)
    def layer_selection_invalid(_request: Request, error: Exception) -> JSONResponse:
        """D143 §7.2: the ``layers`` selection cannot be honoured by this read."""
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid_parameter", "message": str(error)}},
        )

    @app.get("/resolve", response_model=Envelope)
    def resolve(
        deployment: _Deployment,
        name: str,
        context_entity_ids: Annotated[
            list[UUID] | None, Query(max_length=RESOLVE_CONTEXT_LIMIT)
        ] = None,
    ) -> Envelope:
        """Resolve current entities, optionally ranked by focal context (S51)."""
        return deployment.engine.resolve(
            deployment_id=deployment.deployment_id,
            name=name,
            context_entity_ids=tuple(context_entity_ids or ()),
        )

    @app.get("/lookup/relations", response_model=Envelope)
    def lookup_relations(
        deployment: _Deployment,
        subject_entity_id: UUID | None = None,
        predicate: str | None = None,
        object_entity_id: UUID | None = None,
        valid_at: UTCInstant | None = None,
        limit: Annotated[
            int, Query(ge=1, le=LOOKUP_BROWSE_LIMIT_MAX)
        ] = LOOKUP_BROWSE_LIMIT_DEFAULT,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Envelope:
        """Browse relations matching an (s, p, o) pattern — current, or as-of (S9)."""
        try:
            return deployment.engine.lookup_relations(
                deployment_id=deployment.deployment_id,
                subject_entity_id=subject_entity_id,
                predicate=predicate,
                object_entity_id=object_entity_id,
                valid_at=valid_at,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/transcript/relation/{relation_id}", response_model=Envelope)
    def transcript_relation(deployment: _Deployment, relation_id: UUID) -> Envelope:
        """The S8 audit query: why the system believes what it believes."""
        return deployment.engine.transcript_relation(
            deployment_id=deployment.deployment_id, relation_id=relation_id
        )

    @app.get("/lookup/observations", response_model=Envelope)
    def lookup_observations(
        deployment: _Deployment,
        entity_id: UUID,
        property_query: str | None = None,
        k: Annotated[int, Query(ge=1, le=LOOKUP_K_MAX)] = 10,
        limit: Annotated[
            int, Query(ge=1, le=LOOKUP_BROWSE_LIMIT_MAX)
        ] = LOOKUP_BROWSE_LIMIT_DEFAULT,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Envelope:
        """Live observations on one entity (browse or ranked property match)."""
        try:
            return deployment.engine.lookup_observations(
                deployment_id=deployment.deployment_id,
                entity_id=entity_id,
                property_query=property_query,
                k=k,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/search/claims", response_model=Envelope)
    def search_claims(
        deployment: _Deployment,
        query: str,
        k: Annotated[int, Query(ge=1, le=400)] = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Claim search — evidence grain, never current-fact truth."""
        return deployment.engine.search_claims(
            deployment_id=deployment.deployment_id, query=query, k=k, channel=channel
        )

    @app.get("/search/chunks", response_model=Envelope)
    def search_chunks(
        deployment: _Deployment,
        query: str,
        k: Annotated[int, Query(ge=1, le=400)] = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Search live source chunks as separately typed evidence."""
        return deployment.engine.search_chunks(
            deployment_id=deployment.deployment_id, query=query, k=k, channel=channel
        )

    # The same two searches, with the terms in a body instead of the request
    # line. A query is the customer's own words, and a URL is written to
    # access logs, kept by proxies and retained in browser history — so the
    # surface a browser uses must not put it there (D59). The GET forms stay
    # for existing clients, which reach the deployment over a private path.
    @app.post("/search/claims", response_model=Envelope)
    def post_search_claims(
        deployment: _Deployment, body: Annotated[SearchRequest, Body()]
    ) -> Envelope:
        """Claim search — evidence grain, never current-fact truth.

        ``documents`` keeps claims with a live occurrence in a matching
        document (D134); the returned evidence is the claim's origin, as
        without a filter. ``time`` (D140) selects claims of documents with
        declared effective periods through their occurrences in the versions
        in force for the scope, and returns those occurrences.
        """
        return deployment.engine.search_claims(
            deployment_id=deployment.deployment_id,
            query=body.query,
            k=body.k,
            channel=body.channel,
            **_documents_scope(body),
        )

    @app.post("/search/chunks", response_model=Envelope)
    def post_search_chunks(
        deployment: _Deployment, body: Annotated[SearchRequest, Body()]
    ) -> Envelope:
        """Search live source chunks as separately typed evidence.

        ``documents`` keeps chunks whose document version matches (D134);
        ``time`` keeps chunks of versions in force for the scope (D140).
        """
        return deployment.engine.search_chunks(
            deployment_id=deployment.deployment_id,
            query=body.query,
            k=body.k,
            channel=body.channel,
            **_documents_scope(body),
        )

    @app.get("/chunks/{chunk_id}/adjacent", response_model=Envelope)
    def adjacent_chunks(
        deployment: _Deployment,
        chunk_id: UUID,
        window: Annotated[
            int, Query(ge=ADJACENT_CHUNKS_MIN_WINDOW, le=ADJACENT_CHUNKS_MAX_WINDOW)
        ] = 1,
    ) -> Envelope:
        """Fetch surrounding source chunks within a window around a target chunk in document order."""
        return deployment.engine.adjacent_chunks(
            deployment_id=deployment.deployment_id, chunk_id=chunk_id, window=window
        )

    @app.post("/chunks/adjacent", response_model=Envelope)
    def post_adjacent_chunks(
        deployment: _Deployment, body: Annotated[AdjacentChunksRequest, Body()]
    ) -> Envelope:
        """Fetch surrounding source chunks within a window around a target chunk in document order."""
        return deployment.engine.adjacent_chunks(
            deployment_id=deployment.deployment_id,
            chunk_id=body.chunk_id,
            window=body.window,
        )

    @app.get("/hydrate/relation/{relation_id}", response_model=Envelope)
    def hydrate_relation(deployment: _Deployment, relation_id: UUID) -> Envelope:
        """The S5 chain: relation → evidence claims → source documents."""
        return deployment.engine.hydrate_relation(
            deployment_id=deployment.deployment_id, relation_id=relation_id
        )

    if surface is not None:
        _mount_operations(app=app)
    if processing_layers is not None:
        _mount_processing_layers(app=app)
    if open_query is not None:
        _mount_open_query(app=app)
    if ingest is not None:
        _mount_ingest(
            app=app,
            max_body_bytes=ingest_body_max_bytes,
            trusted_principal_source=trusted_principal_source,
        )
        if ingest_body_max_bytes is not None:
            app.add_middleware(_IngestBodyLimit, max_bytes=ingest_body_max_bytes)
    if connectors is not None:
        _mount_connectors(app=app)
    if pipeline_readiness is not None:
        _mount_pipeline_readiness(app=app)
    if documents is not None:
        _mount_document_inventory(app=app)
    if document_search is not None:
        _mount_document_search(app=app)
    if section_history is not None:
        _mount_section_history(app=app)
    if references is not None:
        _mount_references(app=app)
    if document_references is not None:
        _mount_document_references(app=app)
    if deletion is not None:
        _mount_document_deletion(app=app)
    if effective_time is not None:
        _mount_effective_time(app=app)
    if tags is not None:
        _mount_tags(app=app)
    if graph is not None:
        _mount_graph(app=app)
    if build_info is not None:
        _mount_build_info(app=app, tools=_deployment_tools(deployment))

    _install_spend_lease(app=app)
    # Added after the spend lease, so it runs before it: a request's
    # deployment is resolved, then it is authenticated and admitted before any
    # hold is placed, and before routing, so an unknown path is counted like
    # any other.
    app.add_middleware(_PerimeterGate)

    # Last, so it is outermost. Starlette runs middleware in reverse order of
    # addition, and a CORS layer installed early sits *inside* everything
    # added after it — so the body limiter's 413 and the spend lease's 503
    # would return without CORS headers, and a browser would report them as
    # opaque network failures. The whole point of naming an origin is that its
    # errors are legible, and a refusal nobody can read is the one this screen
    # was built to avoid.
    install_browser_origins(app=app, origins=browser_origins)

    return app


#: The 253 characters DNS allows a hostname.
_HOST_MAX_LENGTH = 253

#: The schemes a browser origin may use here, with the port it leaves out.
_DEFAULT_PORTS = {"https": 443, "http": 80}

#: Hosts that name this machine; only these may use plain http.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _canonical_browser_origin(value: str) -> str | None:
    """The origin a browser would send for ``value``, or ``None`` if there is none.

    Deliberately a *small* rule, arrived at after two larger ones were wrong in
    both directions. A pattern enumerating valid hosts kept refusing things
    browsers do send — underscores, trailing dots, IDNA2008 labels the stdlib's
    legacy `idna` codec rejects — while still missing malformed input nobody
    had thought of.

    What this actually guards is a *misconfiguration*: matching is a byte
    comparison, so a non-canonical value grants nothing and silently refuses
    every request while the deployment looks correctly configured. It is not a
    security boundary — a wrong value is closed, never open.

    So the rule checks the things that are unambiguous and cheap: the scheme,
    that nothing but host and port is present, that the value is already
    lowercase as a browser sends it, and that the port is real. Judging
    hostname syntax more finely than that is a job for the DNS resolver, which
    will fail loudly and specifically. The origins actually installed are
    logged at startup so a typo is visible rather than inferred.
    """
    try:
        split = urlsplit(value)
    except ValueError:
        # A malformed bracketed host such as `https://[::::]` — a verdict, not
        # an error to propagate.
        return None
    scheme = split.scheme
    if scheme not in _DEFAULT_PORTS or split.path or split.query or split.fragment:
        return None
    if split.username is not None or split.password is not None:
        return None
    try:
        host, port = split.hostname, split.port
    except ValueError:
        # `urlsplit` refuses a port outside 1-65535.
        return None
    if not host or len(host) > _HOST_MAX_LENGTH or port == 0:
        return None
    # Plain http only for the machine itself: a local app in development
    # (`http://localhost:3000`) never leaves it, so nobody on the network can
    # read or alter what it exchanges. Any other http origin can be.
    if scheme == "http" and host not in _LOOPBACK_HOSTS:
        return None
    # Browsers omit the default port from `Origin`, so `https://host:443`
    # never matches what arrives — the same silent close as a wildcard.
    if port == _DEFAULT_PORTS[scheme]:
        return None
    # A wildcard is the one shape worth naming: an operator who writes it
    # believes they granted a subdomain tree, and they granted nothing.
    if "*" in host:
        return None

    rendered = f"[{host}]" if value.startswith(f"{scheme}://[") else host
    return f"{scheme}://{rendered}" + (f":{port}" if port is not None else "")


def install_browser_origins(*, app: FastAPI, origins: tuple[str, ...]) -> None:
    """Allow named browser origins to call this deployment (D59).

    A browser holding a valid credential still cannot reach a deployment
    without this: the app is served from one origin and the deployment answers
    on its own, so every request is cross-origin and the browser refuses it
    before the credential is ever examined. Authentication was never the
    obstacle; the absence of these headers was.

    **Empty by default, and that is the self-host answer.** A self-hosted
    deployment has no browser app pointed at it, so it advertises nothing and
    no origin is granted anything — adding a permissive default here would
    hand every website on the internet the ability to make authenticated
    requests from a visitor's browser.

    Never `*`: credentials are sent, and the wildcard is invalid with
    credentials in any case. Only the exact origins a deployment was told
    about.
    """
    if not origins:
        return
    for origin in origins:
        if _canonical_browser_origin(origin) != origin:
            raise ValueError(
                "browser origins must each be an https origin (or http on "
                "localhost, 127.0.0.1 or [::1]) exactly as a browser "
                "serializes it — lowercase scheme and host, optional port, "
                f"nothing else; refusing {origin!r}"
            )
    # Said out loud at startup. The validation above is deliberately small, so
    # the operator's own eyes are the last check that the list is what they
    # meant — and a configuration nobody ever sees is one nobody corrects.
    logger.info(
        "browser origins allowed to call this deployment: %s", ", ".join(origins)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        # The credential travels in `Authorization`, not a cookie, so the
        # browser needs no credentialed mode — and turning it on would let a
        # named origin ride a session cookie it should never see.
        # OPTIONS is absent deliberately: the middleware answers preflight
        # itself, so listing it would only advertise a method no route serves.
        # DELETE and PUT are listed because `DELETE /documents/{doc_id}` and
        # the D140 effective-period routes exist; they grant nothing by
        # themselves, since those routes still demand a credential with full
        # write scope.
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        # Exactly the two headers a browser client sends. `Idempotency-Key`
        # was here for a contract nothing implements — advertising a header no
        # route reads invites a client to rely on it.
        allow_headers=["Authorization", "Content-Type"],
        # Starlette's default. Worth knowing that removing an origin leaves a
        # browser's cached preflight usable for up to this long, so revoking
        # the credential is what stops access immediately, not this list.
        max_age=600,
    )


def _served_tools(
    *,
    operations: bool,
    open_query: bool,
    ingest: bool,
    pipeline_readiness: bool,
    deletion: bool,
    document_search: bool,
    section_history: bool,
    document_references: bool = False,
    tags: bool = False,
) -> dict[str, int]:
    """Catalogue tool name → ``tool_version`` for every tool this API serves.

    A tool is listed exactly when the route it calls is composed, so a host
    renders only tools this deployment can answer, at the version it answers
    them (D136 §3.4).
    """
    names: list[str] = []
    if ingest:
        names.append(INGEST_TOOL_NAME)
    if pipeline_readiness:
        names.append(PIPELINE_READINESS_TOOL_NAME)
    if deletion:
        names.append(DELETE_DOCUMENT_TOOL_NAME)
    if document_search:
        names.append(SEARCH_DOCUMENTS_TOOL_NAME)
    if section_history:
        names.append(SECTION_HISTORY_TOOL_NAME)
    if document_references:
        names.append(DOCUMENT_REFERENCES_TOOL_NAME)
    if tags:
        names.extend((*TAG_READ_TOOL_NAMES, *TAG_WRITE_TOOL_NAMES))
    if operations:
        names.extend(OPERATION_TOOL_NAMES)
        names.append(ADJACENT_CHUNKS_TOOL_NAME)
    if open_query:
        names.extend(OPEN_QUERY_TOOL_NAMES)
    return {name: tool(name).tool_version for name in names}


def _deployment_tools(deployment: ApiDeployment) -> dict[str, int]:
    """The catalogue tools whose routes ``deployment`` has capabilities for."""
    return _served_tools(
        operations=deployment.surface is not None,
        open_query=deployment.open_query is not None,
        ingest=deployment.ingest is not None,
        pipeline_readiness=deployment.pipeline_readiness is not None,
        deletion=deployment.deletion is not None,
        document_search=deployment.document_search is not None,
        section_history=deployment.section_history is not None,
        document_references=deployment.document_references is not None,
        tags=deployment.tags is not None,
    )


def _mount_build_info(*, app: FastAPI, tools: dict[str, int]) -> None:
    """Mount `GET /deployment`, the provenance a caller checks before working.

    Composed here rather than added to the app after `build_api` returns, which
    is where it used to live. A route bolted on afterwards is invisible to
    anything reading the surface from this module — the published OpenAPI
    document included, which then describes an API the deployment does not
    match, and did.

    The self-host profile still adds `GET /healthz` that way, deliberately: it
    is the container's liveness probe rather than part of the query API, and it
    is marked `include_in_schema=False` so the published document does not
    offer it as one. Every *documented* route is composed here.

    `tools` is decided by composition, not by the build-info port: which
    catalogue tools this API serves is a fact about the routes mounted here.
    """

    @app.get("/deployment", response_model=DeploymentBuildInfo)
    def deployment_build_info(deployment: _Deployment) -> DeploymentBuildInfo:
        """Report which code, model bindings and tools are serving, before any work."""
        # D143 §7.4: tools that read only an unserved layer are not listed.
        unserved = unserved_operation_names(
            layers=deployment.processing_layers, deployment_id=deployment.deployment_id
        )
        build_info = _served(deployment.build_info)
        # Mounted routes this request's deployment also has capabilities for;
        # adjacent chunks is served by the engine every deployment has.
        served = {*_deployment_tools(deployment), ADJACENT_CHUNKS_TOOL_NAME}
        return build_info.build_info(deployment_id=deployment.deployment_id).model_copy(
            update={
                "tools": {
                    name: version
                    for name, version in tools.items()
                    if name not in unserved and name in served
                }
            }
        )


@contextmanager
def _graph_errors() -> Iterator[None]:
    """Map the live graph's bounded failures to 503, never 500."""
    try:
        yield
    except GraphBusyError as error:
        raise HTTPException(status_code=503, detail="live graph is busy") from error
    except GraphHydrationError as error:
        raise HTTPException(
            status_code=503, detail="live graph result unavailable"
        ) from error
    except (TimeoutError, OperationalError) as error:
        # A statement or lock timeout (or a lost connection) inside the
        # bounded traversal: the graph could not answer in time.
        raise HTTPException(status_code=503, detail="live graph timed out") from error
    except InternalError as error:
        # PostgreSQL reports transaction_timeout (SQLSTATE 25P04) as an
        # internal error class; anything else in that class stays a 500.
        if getattr(error.orig, "sqlstate", None) != _TRANSACTION_TIMEOUT:
            raise
        raise HTTPException(status_code=503, detail="live graph timed out") from error


_TRANSACTION_TIMEOUT: Final = "25P04"


_LAYERS_HELP: Final = (
    "D143: the processing layers this request may read (sources, claims,"
    " facts); default every layer the deployment serves."
)


def _layer_selection(
    layers: list[ProcessingLayer] | None,
) -> frozenset[ProcessingLayer] | None:
    """The request's ``layers`` query parameter as a selection, or None."""
    return None if layers is None else frozenset(layers)


def _require_facts(
    *, layers: ProcessingLayersPort | None, deployment_id: UUID
) -> LayerAdmission:
    """Admit an entity-graph read: refused without facts, disclosed while backfilling."""
    state = (
        "enabled"
        if layers is None
        else layers.states(deployment_id=deployment_id).facts
    )
    if state == "disabled":
        raise LayerUnavailableError(
            layer=ProcessingLayer.FACTS,
            message="this deployment does not process the facts layer",
        )
    return LayerAdmission(
        consulted=_FACTS_LAYER,
        backfilling=_FACTS_LAYER if state == "backfilling" else frozenset(),
    )


_FACTS_LAYER: Final = frozenset({ProcessingLayer.FACTS})


def _mount_graph(*, app: FastAPI) -> None:
    """Mount the three server-owned graph operations.

    The entity graph reads the facts layer; citation paths read sources
    (D143 §7.2).
    """

    @app.post("/graph/neighborhood", response_model=Envelope)
    def graph_neighborhood(
        deployment: _Deployment, body: GraphNeighborhoodRequest
    ) -> Envelope:
        """Return a current or bitemporal bounded entity neighborhood."""
        graph = _served(deployment.graph)
        admission = _require_facts(
            layers=deployment.processing_layers, deployment_id=deployment.deployment_id
        )
        with _graph_errors():
            envelope = graph.neighborhood(
                entity_id=body.entity_id,
                hops=body.hops,
                predicates=body.predicates,
                valid_at=body.valid_at,
                believed_at=body.believed_at,
                limit=body.limit,
                cursor=body.cursor,
                include_paths=body.include_paths,
            )
        # D143 §7.3: an empty answer while facts backfill is a boundary.
        return disclose_layers(envelope=envelope, admission=admission)

    @app.post("/graph/path", response_model=Envelope)
    def graph_path(deployment: _Deployment, body: GraphPathRequest) -> Envelope:
        """Return bounded equal-length shortest paths between two entities."""
        graph = _served(deployment.graph)
        admission = _require_facts(
            layers=deployment.processing_layers, deployment_id=deployment.deployment_id
        )
        with _graph_errors():
            envelope = graph.path(
                from_entity_id=body.from_entity_id,
                to_entity_id=body.to_entity_id,
                max_hops=body.max_hops,
                predicates=body.predicates,
                valid_at=body.valid_at,
                believed_at=body.believed_at,
            )
        return disclose_layers(envelope=envelope, admission=admission)

    @app.post("/graph/citation-path", response_model=Envelope)
    def graph_citation_path(
        deployment: _Deployment, body: GraphCitationPathRequest
    ) -> Envelope:
        """Return bounded directed citation paths between two documents."""
        graph = _served(deployment.graph)
        with _graph_errors():
            return graph.citation_path(
                from_doc_id=body.from_doc_id,
                to_doc_id=body.to_doc_id,
                max_hops=body.max_hops,
            )


def _mount_open_query(*, app: FastAPI) -> None:
    """Add the seven §3.1 open-query routes when the facade is composed.

    Paths are short and consistent under `/query/…`. Sandbox and registry failures map to typed
    HTTP status + public error code without private engine detail.

    When the request's deployment has a perimeter, every execution-bearing
    route forwards ``AuthenticatedContext.principal`` into the facade.
    Content-only discovery and metadata routes stay content-free and do not
    invent principals. Without auth, the facade default principal is
    unchanged.
    """
    principal_dep = _open_query_principal

    @app.post("/query/sql", response_model=QueryResult)
    def query_sql(
        deployment: _Deployment,
        body: SqlQueryRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
        layers: Annotated[
            list[ProcessingLayer] | None, Query(description=_LAYERS_HELP)
        ] = None,
    ) -> QueryResult:
        """One sandboxed SQL statement; QueryResult/v1."""
        open_query = _served(deployment.open_query)
        return _open_call(
            lambda: open_query.query_sql(
                sql=body.sql,
                parameters=body.parameters,
                max_rows=body.max_rows,
                principal=principal,
                layers=_layer_selection(layers),
            )
        )

    @app.post("/query/sql/explain", response_model=QueryResult)
    def explain_sql(
        deployment: _Deployment,
        body: SqlExplainRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
        layers: Annotated[
            list[ProcessingLayer] | None, Query(description=_LAYERS_HELP)
        ] = None,
    ) -> QueryResult:
        """EXPLAIN one SQL statement without executing it."""
        open_query = _served(deployment.open_query)
        return _open_call(
            lambda: open_query.explain_sql(
                sql=body.sql,
                parameters=body.parameters,
                principal=principal,
                layers=_layer_selection(layers),
            )
        )

    @app.get("/query/space")
    def describe_query_space(
        deployment: _Deployment,
        pattern: str | None = None,
        include_examples: bool = False,
    ) -> dict[str, object]:
        """Manifest-backed schema discovery (content-free)."""
        open_query = _served(deployment.open_query)
        from rememberstack.surfaces.query_sandbox.discovery import (
            query_space_description_payload,
        )

        description = _open_call(
            lambda: open_query.describe_query_space(
                pattern=pattern, include_examples=include_examples
            )
        )
        return query_space_description_payload(description)

    @app.get("/query/space/search")
    def search_query_space(
        deployment: _Deployment,
        query: Annotated[str, Query(min_length=1)],
        k: Annotated[int, Query(ge=1, le=25)] = 10,
    ) -> list[dict[str, object]]:
        """Search checked-in manifest text only."""
        open_query = _served(deployment.open_query)
        hits = _open_call(lambda: open_query.search_query_space(query=query, k=k))
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

    @app.get("/query/saved")
    def list_saved_queries(
        deployment: _Deployment,
        namespace: str | None = None,
        status: SavedQueryStatus | None = None,
    ) -> list[dict[str, object]]:
        """Registry metadata for discoverable saved queries."""
        open_query = _served(deployment.open_query)
        rows = _open_call(
            lambda: open_query.list_saved_queries(namespace=namespace, status=status)
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

    @app.get("/query/saved/{namespace}/{name}")
    def describe_saved_query(
        deployment: _Deployment, namespace: str, name: str, version: int | None = None
    ) -> dict[str, object]:
        """One immutable saved-query version."""
        open_query = _served(deployment.open_query)
        detail = _open_call(
            lambda: open_query.describe_saved_query(
                namespace=namespace, name=name, version=version
            )
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

    @app.post("/query/saved/{namespace}/{name}/run", response_model=QueryResult)
    def run_saved_query(
        deployment: _Deployment,
        namespace: str,
        name: str,
        body: RunSavedQueryRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
        layers: Annotated[
            list[ProcessingLayer] | None, Query(description=_LAYERS_HELP)
        ] = None,
    ) -> QueryResult:
        """Execute one active saved query through the same SQL executor."""
        open_query = _served(deployment.open_query)
        return _open_call(
            lambda: open_query.run_saved_query(
                namespace=namespace,
                name=name,
                version=body.version,
                parameters=body.parameters,
                max_rows=body.max_rows,
                principal=principal,
                layers=_layer_selection(layers),
            )
        )


def _open_call(action):  # noqa: ANN001, ANN202
    """Run one open-query action, mapping typed sandbox failures to HTTP."""
    try:
        return action()
    except SandboxRejection as error:
        raise HTTPException(
            status_code=_sandbox_status(error.code),
            detail={"code": error.code.value, "message": error.message},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_parameter", "message": str(error)}
        ) from error


def _sandbox_status(code: QueryErrorCode) -> int:
    """Map public sandbox codes to stable HTTP statuses without private detail."""
    if code is QueryErrorCode.SAVED_QUERY_NOT_FOUND:
        return 404
    if code in (
        QueryErrorCode.SAVED_QUERY_DISABLED,
        QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
        QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
        QueryErrorCode.QUOTA_EXCEEDED,
        QueryErrorCode.CONCURRENCY_EXCEEDED,
        QueryErrorCode.SCHEMA_VERSION_MISMATCH,
    ):
        return 409
    if code in (
        QueryErrorCode.PG_UNAVAILABLE,
        QueryErrorCode.P1_UNAVAILABLE,
        QueryErrorCode.GRAPH_UNAVAILABLE,
        QueryErrorCode.CORPUS_BODY_UNAVAILABLE,
        QueryErrorCode.GENERATION_UNAVAILABLE,
    ):
        return 503
    if code in (
        QueryErrorCode.STATEMENT_TIMEOUT,
        QueryErrorCode.LOCK_TIMEOUT,
        QueryErrorCode.CANCELLED,
        QueryErrorCode.RESOURCE_LIMIT,
        QueryErrorCode.EXECUTION_ERROR,
        QueryErrorCode.CONFIRMATION_FAILED,
    ):
        return 500
    return 422


def _mount_pipeline_readiness(*, app: FastAPI) -> None:
    """Expose a normal, read-only completion boundary for bounded versions."""

    @app.post("/readiness", response_model=PipelineReadinessReport)
    def pipeline_readiness(
        deployment: _Deployment, request: Annotated[PipelineReadinessRequest, Body()]
    ) -> PipelineReadinessReport:
        """Inspect exact pipeline and explicitly requested capabilities."""
        return _served(deployment.pipeline_readiness).inspect(
            deployment_id=deployment.deployment_id,
            version_ids=tuple(request.version_ids),
            require=request.require,
        )


def _mount_document_inventory(*, app: FastAPI) -> None:
    """Expose the read-only inventory of what this deployment holds."""

    @app.get("/documents", response_model=DocumentPage)
    def list_documents(
        deployment: _Deployment,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[str | None, Query()] = None,
        status: Annotated[DocumentStatusFilter | None, Query()] = None,
    ) -> DocumentPage:
        """One page of documents, newest lineage first.

        Ordered by when each document was *first seen*, which never changes —
        re-ingesting one does not move it up. An activity order would, and a
        keyset cursor against a moving key drops and repeats rows.

        `status` filters on the *newest* version's state, which is what makes
        "show me what failed" answerable in one call rather than by paging the
        whole corpus and filtering client-side.
        """
        try:
            return _served(deployment.documents).list_documents(
                deployment_id=deployment.deployment_id,
                limit=limit,
                cursor=cursor,
                status=status,
            )
        except ValueError as error:
            # A cursor that does not parse. 400 rather than a silent restart:
            # returning page one would look like the corpus repeating itself.
            raise HTTPException(status_code=400, detail=str(error)) from error


def _documents_scope(body: SearchRequest) -> dict[str, Any]:
    """The D134 ``documents`` and D140 ``time`` keywords, only when present."""
    scope: dict[str, Any] = {}
    if body.documents is not None:
        scope["documents"] = body.documents
    if body.time is not None:
        scope["time"] = body.time
    return scope


def _mount_document_search(*, app: FastAPI) -> None:
    """Expose ``search_documents`` (D134): find files. A read."""

    @app.post(
        "/documents/search",
        response_model=DocumentSearchPage,
        responses={400: {"description": "cursor is malformed"}},
    )
    def search_documents(
        deployment: _Deployment, body: Annotated[DocumentSearchRequest, Body()]
    ) -> DocumentSearchPage:
        """Find documents by observed name, general metadata and content.

        Each result is a document judged by one version: its current version
        by default, or with ``versions: all`` the newest live version that
        matches. With a ``query`` results are ranked and not paged; with
        filters only they are ordered by declared creation date and paged by
        ``cursor``, which pins the first call's as-of instant.
        """
        try:
            return _served(deployment.document_search).search_documents(
                deployment_id=deployment.deployment_id, request=body
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


def _mount_section_history(*, app: FastAPI) -> None:
    """Expose ``section_history`` (D140 §6.2). A read."""

    @app.get(
        "/documents/{doc_id}/sections/{section_key:path}/history",
        response_model=SectionHistoryPage,
        responses={
            400: {"description": "cursor is malformed or belongs to another call"},
            404: {"description": "document_not_found"},
            422: {"description": "invalid section key or time scope"},
        },
    )
    def section_history(
        deployment: _Deployment,
        doc_id: UUID,
        section_key: str,
        mode: Literal["current", "at", "overlap", "history"] = "history",
        at: datetime | None = None,
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to: datetime | None = None,
        limit: Annotated[
            int, Query(ge=1, le=SECTION_HISTORY_MAX_LIMIT)
        ] = SECTION_HISTORY_DEFAULT_LIMIT,
        cursor: Annotated[str | None, Query(min_length=1)] = None,
    ) -> SectionHistoryPage:
        """One section key across the document's versions.

        One row per version the time scope selects (``mode``, default
        ``history``; ``at`` for ``mode=at``, ``from``/``to`` for
        ``mode=overlap``): the version, its in-force intervals, and the
        section it holds under the key with ``changed``/``own_changed``
        against the previous row that held it — or ``absent``,
        ``not_indexed`` or ``processing``. Ordered by effective start for a
        periodised document, else by version number; ``limit`` / ``cursor``
        page and pin the evaluation and belief instants (``next_cursor`` out).
        """
        time: dict[str, object] = {"mode": mode}
        if mode == "at":
            time["at"] = at
        elif mode == "overlap":
            time["from"] = from_
            time["to"] = to
        try:
            request = SectionHistoryRequest.model_validate(
                {
                    "doc_id": doc_id,
                    "section_key": section_key,
                    "time": time,
                    "limit": limit,
                    "cursor": cursor,
                }
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=422,
                detail=error.errors(include_url=False, include_context=False),
            ) from error
        try:
            return _served(deployment.section_history).section_history(
                deployment_id=deployment.deployment_id, request=request
            )
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="document_not_found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


def _reference_errors(error: Exception) -> HTTPException:
    """Map one references refusal to its status and detail."""
    if isinstance(error, DocumentNotFoundError):
        return HTTPException(status_code=404, detail="document_not_found")
    if isinstance(error, DocumentVersionNotFoundError):
        return HTTPException(status_code=404, detail="version_not_found")
    if isinstance(error, ReferenceBodyError):
        return HTTPException(
            status_code=422,
            detail={"code": error.code, "line": error.line, "message": error.reason},
        )
    raise error


_REFERENCE_ERRORS: Final = (
    DocumentNotFoundError,
    DocumentVersionNotFoundError,
    ReferenceBodyError,
)


def _mount_references(*, app: FastAPI) -> None:
    """Expose the supplied-reference write (write scope) and its read (D140 §6.3)."""

    @app.put(
        "/documents/{doc_id}/versions/{version_id}/references",
        response_model=ReferencesSet,
        responses={
            404: {"description": "document_not_found | version_not_found"},
            413: {"description": "reference_set_too_large (over 64 MiB)"},
            422: {"description": "invalid_reference_set: the failing line"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/x-ndjson": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def set_references(
        deployment: _Deployment, doc_id: UUID, version_id: UUID, request: Request
    ) -> ReferencesSet:
        """Replace one version's supplied references with an NDJSON set.

        One reference per line: ``kind``, ``from_section_key``, ``target``
        (``source_kind``, ``source_ref``, ``version_key``, ``section_key``),
        ``binding`` (``floating`` or ``pinned``), ``change_effective_from``
        and ``change_date_known`` (for ``amends``), ``source_label``,
        ``context``. The latest PUT wins: an equal set is a retry, a set
        equal to the active one cancels a newer pending one, and any other
        set becomes a pending generation the pipeline validates
        (all-or-nothing against the version's section keys) and activates.
        A malformed line is 422 naming it; more than 64 MiB is 413.
        """
        declared = request.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > REFERENCES_BODY_MAX_BYTES
        ):
            raise _references_too_large()
        received = bytearray()
        async for chunk in request.stream():
            received.extend(chunk)
            if len(received) > REFERENCES_BODY_MAX_BYTES:
                raise _references_too_large()
        try:
            return await run_in_threadpool(
                _served(deployment.references).set_references,
                deployment_id=deployment.deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                body=bytes(received),
            )
        except _REFERENCE_ERRORS as error:
            raise _reference_errors(error) from error

    @app.get(
        "/documents/{doc_id}/versions/{version_id}/references",
        response_model=ReferenceGenerations,
        responses={404: {"description": "document_not_found | version_not_found"}},
    )
    def reference_generations(
        deployment: _Deployment, doc_id: UUID, version_id: UUID
    ) -> ReferenceGenerations:
        """The version's reference generations, newest first.

        Each generation is ``pending``, ``active``, ``rejected`` (with
        ``{item, field, reason}`` errors) or ``superseded``.
        """
        try:
            return _served(deployment.references).reference_generations(
                deployment_id=deployment.deployment_id,
                doc_id=doc_id,
                version_id=version_id,
            )
        except _REFERENCE_ERRORS as error:
            raise _reference_errors(error) from error


def _references_too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={
            "code": "reference_set_too_large",
            "limit_bytes": REFERENCES_BODY_MAX_BYTES,
        },
    )


def _mount_document_references(*, app: FastAPI) -> None:
    """Expose ``document_references`` (D140 §6.2). A read."""

    @app.post(
        "/documents/references",
        response_model=DocumentReferencesPage,
        responses={
            400: {"description": "cursor is malformed or belongs to another call"},
            404: {"description": "document_not_found | chunk_not_found"},
        },
    )
    def document_references(
        deployment: _Deployment, body: DocumentReferencesRequest
    ) -> DocumentReferencesPage:
        """What a passage, section or document references, and what references it.

        Give ``chunk_id`` or ``doc_id`` (optionally ``section_key``). Each row
        is one reference in one source window, resolved against the target
        versions in force then: ``applies_during``, ``concurrent`` and a
        status (``resolved``, ``target_processing``, ``target_unavailable``,
        ``target_not_in_force``, ``section_not_in_version``,
        ``section_not_indexed``, ``pinned_version_unavailable``). ``cursor``
        pages and pins the evaluation and belief instants.
        """
        try:
            return _served(deployment.document_references).document_references(
                deployment_id=deployment.deployment_id, request=body
            )
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="document_not_found") from error
        except ChunkNotFoundError as error:
            raise HTTPException(status_code=404, detail="chunk_not_found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


def _mount_document_deletion(*, app: FastAPI) -> None:
    """Expose the lineage-grain delete (D135). Requires write scope."""

    @app.delete(
        "/documents/{doc_id}",
        response_model=DocumentDeletion,
        responses={404: {"description": "document_not_found"}},
    )
    def delete_document(deployment: _Deployment, doc_id: UUID) -> DocumentDeletion:
        """Remove one document from the live memory.

        Its claims stop counting as current testimony, facts that no other
        document supports are closed with a recorded retraction, and it
        leaves the inventory and every read. The claims and the stored
        original are kept as history: this is not an erasure.

        The deletion is all or nothing. An unknown id and an already deleted
        document are both 404: from the caller's side each is absent. A
        document hidden by another path whose evidence was never updated is
        finished and answers 200 instead.
        """
        try:
            return _served(deployment.deletion).delete_document(
                deployment_id=deployment.deployment_id, doc_id=doc_id
            )
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="document_not_found") from error
        except ForgetInProgressError as error:
            # Admission was open when the request arrived, but a hard forget
            # was already preparing when the delete took the D74 fence. The
            # delete ran no statement; answer exactly as admission would.
            raise HTTPException(
                status_code=503, detail={"code": "forget_in_progress"}
            ) from error


def _effective_time_errors(error: Exception) -> HTTPException:
    """Map one D140 write refusal to its status and structured detail."""
    if isinstance(error, DocumentNotFoundError):
        return HTTPException(status_code=404, detail="document_not_found")
    if isinstance(error, DocumentVersionNotFoundError):
        return HTTPException(status_code=404, detail="version_not_found")
    if isinstance(error, EffectivePeriodConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "effective_period_conflict",
                "message": str(error),
                "version_id": str(error.version_id),
                "effective_from": error.effective_from.isoformat(),
            },
        )
    if isinstance(error, VersionKeyConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "version_key_conflict",
                "message": str(error),
                "version_id": str(error.version_id),
                "version_key": error.version_key,
            },
        )
    if isinstance(error, EffectiveTimeNotSupportedError):
        return HTTPException(
            status_code=422,
            detail={"code": "effective_time_requires_snapshot", "message": str(error)},
        )
    raise error


_EFFECTIVE_TIME_ERRORS: Final = (
    DocumentNotFoundError,
    DocumentVersionNotFoundError,
    EffectivePeriodConflictError,
    VersionKeyConflictError,
    EffectiveTimeNotSupportedError,
)


def _mount_effective_time(*, app: FastAPI) -> None:
    """Expose the D140 period writes. Both require write scope."""

    @app.put(
        "/documents/{doc_id}/versions/{version_id}/effective-periods",
        response_model=EffectivePeriodsSet,
        responses={
            404: {"description": "document_not_found | version_not_found"},
            409: {"description": "effective_period_conflict"},
            422: {"description": "effective_time_requires_snapshot"},
        },
    )
    def set_effective_periods(
        deployment: _Deployment,
        doc_id: UUID,
        version_id: UUID,
        body: EffectivePeriodsRequest,
    ) -> EffectivePeriodsSet:
        """Replace the periods during which one version's text is in force.

        The body is the version's complete set: declarations not listed are
        retracted and new ones declared, atomically. A period without
        ``effective_until`` lasts until the next declared start in the
        document. An empty set leaves the version with no in-force period and
        the document keeps its declared effective time (use ``DELETE
        /documents/{doc_id}/effective-periods`` to leave it). A start already
        declared for another version of the document is refused with 409, and
        a ``living`` document with 422. Periods never trigger reprocessing.
        """
        try:
            return _served(deployment.effective_time).set_effective_periods(
                deployment_id=deployment.deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                periods=body.periods,
            )
        except _EFFECTIVE_TIME_ERRORS as error:
            raise _effective_time_errors(error) from error

    @app.delete(
        "/documents/{doc_id}/effective-periods",
        response_model=EffectiveTimeCleared,
        responses={404: {"description": "document_not_found"}},
    )
    def clear_effective_time(
        deployment: _Deployment, doc_id: UUID
    ) -> EffectiveTimeCleared:
        """Return a document to "the newest processed version is current".

        Every declared period of the document is retracted and the change is
        recorded, so reads pinned to an earlier instant still see the periods
        they saw. It is the only way back from declared effective time.
        """
        try:
            return _served(deployment.effective_time).clear_effective_time(
                deployment_id=deployment.deployment_id, doc_id=doc_id
            )
        except _EFFECTIVE_TIME_ERRORS as error:
            raise _effective_time_errors(error) from error


def _tag_errors(error: Exception) -> HTTPException:
    """Map one D156 refusal to its status and structured detail."""
    if isinstance(error, UnknownTagError):
        return HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "tags": list(error.tags),
            },
        )
    if isinstance(error, TagConflictError | TagLimitError):
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, DocumentNotFoundError):
        return HTTPException(status_code=404, detail="document_not_found")
    raise error


_TAG_ERRORS: Final = (
    UnknownTagError,
    TagConflictError,
    TagLimitError,
    DocumentNotFoundError,
)


def _mount_tags(*, app: FastAPI) -> None:
    """Expose the D156 tag registry and document tag writes.

    `GET /tags` is a read; every other route requires write scope. Routes
    that address one tag take its ``tag_id``, because names may contain
    ``/``; the SDK, CLI and MCP tools accept a name and resolve it first.
    """

    @app.get("/tags", response_model=TagList)
    def list_tags(
        deployment: _Deployment,
        query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        name: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=TAG_REF_MAX_RAW_LEN,
                description=(
                    "Keep only the tag with exactly this name, matched as the"
                    " registry matches names (NFC, ignoring letter case)."
                ),
            ),
        ] = None,
    ) -> TagList:
        """Every registered tag with its id, name and description, by name.

        ``query`` keeps the tags whose name or description contains it,
        ignoring case; ``name`` keeps the one tag with exactly that name, so a
        client resolves a name to its ``tag_id`` the way the engine does. No
        document counts are returned.
        """
        return _served(deployment.tags).list_tags(
            deployment_id=deployment.deployment_id, query=query, name=name
        )

    @app.post(
        "/tags",
        response_model=Tag,
        responses={409: {"description": "conflict | tag_limit_exceeded"}},
    )
    def create_tag(deployment: _Deployment, body: TagCreate) -> Tag:
        """Register a tag before it is used.

        The name keeps the spelling it is declared with and is unique ignoring
        case: declaring ``Project:A`` when ``project:a`` exists is 409
        ``conflict``.
        """
        try:
            return _served(deployment.tags).create_tag(
                deployment_id=deployment.deployment_id, request=body
            )
        except _TAG_ERRORS as error:
            raise _tag_errors(error) from error

    @app.patch(
        "/tags/{tag_id}",
        response_model=Tag,
        responses={
            404: {"description": "unknown_tag"},
            409: {"description": "conflict"},
        },
    )
    def update_tag(deployment: _Deployment, tag_id: UUID, body: TagUpdate) -> Tag:
        """Rename or re-describe a tag; the documents carrying it keep it.

        The old name stops resolving at once: there are no aliases, so a
        caller still using it gets ``unknown_tag``.
        """
        try:
            return _served(deployment.tags).update_tag(
                deployment_id=deployment.deployment_id, tag=str(tag_id), request=body
            )
        except _TAG_ERRORS as error:
            raise _tag_errors(error) from error

    @app.delete(
        "/tags/{tag_id}",
        response_model=Tag,
        responses={404: {"description": "unknown_tag"}},
    )
    def delete_tag(deployment: _Deployment, tag_id: UUID) -> Tag:
        """Remove a tag and every assignment of it, in one write."""
        try:
            return _served(deployment.tags).delete_tag(
                deployment_id=deployment.deployment_id, tag=str(tag_id)
            )
        except _TAG_ERRORS as error:
            raise _tag_errors(error) from error

    @app.post(
        "/documents/{doc_id}/tags",
        response_model=DocumentTags,
        responses={
            404: {"description": "document_not_found"},
            409: {"description": "tag_limit_exceeded"},
            422: {"description": "unknown_tag"},
        },
    )
    def update_document_tags(
        deployment: _Deployment, doc_id: UUID, body: DocumentTagsUpdate
    ) -> DocumentTags:
        """Add and remove a document's tags, or replace them with ``set``.

        One atomic write. ``set`` excludes ``add`` and ``remove``; an empty
        ``set`` clears the tags. Removing a tag the document does not have is
        not an error. Any unregistered tag refuses the whole request with 422
        ``unknown_tag`` naming every one. Tags never cause processing.
        """
        try:
            return _served(deployment.tags).update_document_tags(
                deployment_id=deployment.deployment_id, doc_id=doc_id, request=body
            )
        except _TAG_ERRORS as error:
            raise _tag_errors(error) from error


def _mount_processing_layers(*, app: FastAPI) -> None:
    """Mount ``GET /processing-layers`` (D143 §7.4)."""

    @app.get("/processing-layers", response_model=ProcessingLayerStates)
    def processing_layers(deployment: _Deployment) -> ProcessingLayerStates:
        """Each switchable layer is enabled, backfilling or disabled."""
        return _served(deployment.processing_layers).states(
            deployment_id=deployment.deployment_id
        )


def _mount_operations(*, app: FastAPI) -> None:
    """Add the registry-rendered assured-operation endpoints (D50/D87)."""

    @app.get("/operations", response_model=list[ToolDescriptor])
    def list_operations(deployment: _Deployment) -> list[ToolDescriptor]:
        """The four assured operations for this deployment."""
        surface = _served(deployment.surface)
        # D143 §7.4: operations that read only an unserved layer are not listed.
        unserved = unserved_operation_names(
            layers=deployment.processing_layers, deployment_id=deployment.deployment_id
        )
        return [
            descriptor
            for descriptor in surface.descriptors()
            if descriptor.name not in unserved
        ]

    @app.post("/operations/{name}", response_model=Envelope | ContextBundleV2)
    def run_operation(
        deployment: _Deployment,
        name: str,
        arguments: Annotated[dict[str, object], Body(default_factory=dict)],
        request: Request,
        layers: Annotated[
            list[ProcessingLayer] | None, Query(description=_LAYERS_HELP)
        ] = None,
    ) -> Envelope | ContextBundleV2:
        """Run one assured operation by name over JSON arguments.

        The route-level table cannot classify this one: operations are registry
        data, and whether a given operation mutates is a property of the
        operation rather than of the path. So the scope check happens here,
        against the descriptor, and an operation that has not declared itself
        non-mutating is refused to a read-only credential.
        """
        surface = _served(deployment.surface)
        context = getattr(request.state, "perimeter_context", None)
        if context is not None:
            descriptor = next(
                (item for item in surface.descriptors() if item.name == name), None
            )
            required = operation_scope(
                mutates=descriptor.mutates if descriptor is not None else None
            )
            if not context.may(required=required):
                raise HTTPException(
                    status_code=403, detail="credential may not perform this operation"
                )
        try:
            return surface.run(
                name=name, arguments=arguments, layers=_layer_selection(layers)
            )
        except UnknownOperationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (MissingArgumentError, InvalidArgumentError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": str(error)},
            ) from error


class _IngestBodyLimit:
    """ASGI guard: refuse over-cap `POST /ingest` bodies before buffering.

    The Content-Length declaration is trustworthy because the ASGI server
    enforces HTTP framing (a body larger than its declared length is a
    protocol error), so the guard needs no streaming byte counter — it
    requires a declared length (411 without one) and refuses any declared
    length over the cap (413) before FastAPI reads the body into memory.
    """

    def __init__(self, app: object, *, max_bytes: int) -> None:
        """Wrap the inner ASGI app with one configured byte ceiling."""
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        """Reject oversized or length-less ingest requests; pass the rest."""
        path = str(scope.get("path", ""))
        root_path = str(scope.get("root_path", ""))
        if root_path and path.startswith(root_path):
            # a mounted or root_path-prefixed app still routes /ingest; the
            # guard must see the same route FastAPI will, or a prefixed
            # deployment would buffer unbounded bodies past it
            path = path[len(root_path) :]
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or path != "/ingest"
        ):
            await self._app(scope, receive, send)  # type: ignore[operator]
            return
        declared: int | None = None
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break
        if declared is None:
            await _send_json_error(send=send, status=411, detail="length_required")
            return
        if declared > self._max_bytes:
            await _send_json_error(send=send, status=413, detail="body_too_large")
            return
        await self._app(scope, receive, send)  # type: ignore[operator]


async def _send_json_error(*, send: object, status: int, detail: str) -> None:
    """Emit one small JSON error response from ASGI middleware."""
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(  # type: ignore[operator]
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})  # type: ignore[operator]


def _parse_ingest_principal(
    *, kind: str | None, ref: str | None
) -> IngestPrincipal | None:
    """Validate a trusted attribution pair, or raise a 422 explaining why.

    Only reached on a trusted network perimeter and, where API authentication
    is configured, one presenting full WRITE authority. A malformed pair here
    is therefore a real client error worth reporting rather than metadata that
    should be dropped.
    """
    if kind is None and ref is None:
        return None
    if kind is None or ref is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "X-Ingest-Principal-Kind and X-Ingest-Principal-Ref must be"
                " supplied together"
            ),
        )
    try:
        return IngestPrincipal(kind=IngestPrincipalKind(kind), external_ref=ref)
    except ValueError as error:
        # ValueError covers the unknown-kind enum miss and the model's
        # printable-ASCII/length rules. Without this the model error escaped
        # as a 500 — a metadata field crashing the request.
        raise HTTPException(
            status_code=422, detail="invalid_ingest_principal"
        ) from error


def _forgotten_source_http_error() -> HTTPException:
    """A hard forget covers these bytes or this source identity; it is permanent."""
    return HTTPException(status_code=409, detail="source_forgotten")


def _mount_ingest(
    *, app: FastAPI, max_body_bytes: int | None, trusted_principal_source: bool = False
) -> None:
    """Add the D62 lineage-aware push surface over the E0 ingest gate."""

    @app.post("/ingest", response_model=IngestedVersion)
    def ingest_document(
        deployment: _Deployment,
        request: Request,
        content: Annotated[bytes, Body(media_type="application/octet-stream")],
        filename: Annotated[str, Query(min_length=1)],
        mime: Annotated[str, Query(min_length=1)],
        title: str | None = None,
        source_kind: Annotated[str | None, Query(min_length=1)] = None,
        source_ref: Annotated[str | None, Query(min_length=1)] = None,
        source_modified_at: datetime | None = None,
        versioning_mode: Literal["snapshot", "living"] = "snapshot",
        source_version_ref: str | None = None,
        source_path: Annotated[
            str | None,
            Query(
                min_length=1,
                description=(
                    "Where the file lives at its source (a folder path or URL)."
                    " Recorded with this version's metadata and observed"
                    " names, so the document can later be found by it."
                ),
            ),
        ] = None,
        version_key: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=VERSION_KEY_MAX_LEN,
                description=(
                    "Your immutable key for this version, unique within the"
                    " document (for example a publisher's edition id). A new"
                    " key always creates a version; an existing key is"
                    " accepted only when re-sending the latest version's"
                    " bytes. Requires source_kind/source_ref."
                ),
            ),
        ] = None,
        effective_from: Annotated[
            datetime | None,
            Query(
                description=(
                    "Start (inclusive, UTC) of the period this version's text"
                    " is in force for. Requires source_kind/source_ref and"
                    " versioning_mode=snapshot."
                )
            ),
        ] = None,
        effective_until: Annotated[
            datetime | None,
            Query(
                description=(
                    "Declared end (exclusive, UTC) of that period; without it"
                    " the period lasts until the next declared start."
                )
            ),
        ] = None,
        tags: Annotated[
            list[TagRef] | None,
            Query(
                max_length=TAGS_PER_DOCUMENT_MAX,
                description=(
                    "Registered tags (name or tag_id; repeat the parameter)"
                    " added to the document's tags. Ingest never removes a"
                    " tag, and tags never decide whether a version is"
                    " created. Any unregistered tag refuses the ingest with"
                    " 422 unknown_tag before anything is stored."
                ),
            ),
        ] = None,
        principal_kind: Annotated[
            str | None,
            Header(
                alias="X-Ingest-Principal-Kind",
                # Bound raw so an untrusted deployment can discard the pair
                # before validating it; the real contract is described here
                # because request-time validation would let malformed
                # metadata fail an ingest that should simply ignore it.
                description=(
                    "One of: user | api_credential | service. Sent with"
                    " X-Ingest-Principal-Ref. Ignored unless the deployment"
                    " declares a trusted principal source and, where API"
                    " authentication is configured, the presenting credential"
                    " carries full write authority; malformed values are 422"
                    " only for a trusted assertion."
                ),
            ),
        ] = None,
        principal_ref: Annotated[
            str | None,
            Header(
                alias="X-Ingest-Principal-Ref",
                description=(
                    "Opaque caller-stable actor id, 1..255 printable ASCII"
                    " characters. Sent with X-Ingest-Principal-Kind."
                ),
            ),
        ] = None,
    ) -> IngestedVersion:
        """Push one file through E0, optionally as a stable lineage version.

        Attribution travels in **headers, never the query string**: the
        reference is erasable PII and a URL is copied verbatim into access
        logs, proxies and traces, where a later principal deletion cannot
        reach it.

        The pair is honoured only when the composing profile declares its
        network perimeter trusted (``trusted_principal_source``) and, where API
        authentication is configured, the presenting credential carries full
        WRITE authority. The unscoped shared secret is unrestricted; a narrow
        signed ``ingest`` credential may add a document but cannot nominate its
        principal. Untrusted attribution is **ignored, not rejected**: nothing
        forged is recorded either way, and refusing would let a metadata
        concern fail an otherwise valid ingest.
        """
        ingest = _served(deployment.ingest)
        deployment_id = deployment.deployment_id
        if max_body_bytes is not None and len(content) > max_body_bytes:
            # the ASGI guard already refused honest requests; this backstop
            # holds if a server ever passes an unframed oversized body through
            raise HTTPException(status_code=413, detail="body_too_large")
        if (source_kind is None) != (source_ref is None):
            raise HTTPException(
                status_code=422,
                detail="source_kind and source_ref must be supplied together",
            )
        # The headers are taken RAW and are not validated at request binding.
        # An untrusted source must discard them without inspection: if malformed
        # attribution could 422, a metadata concern would still fail an
        # otherwise valid upload, which is exactly what ignoring is for. A
        # configured perimeter publishes its context before this handler; only
        # full WRITE authority may make the immutable attribution assertion.
        context = getattr(request.state, "perimeter_context", None)
        attribution_requires_write = deployment.auth is not None
        may_assert_principal = not attribution_requires_write or (
            context is not None and context.may(required=PerimeterScope.WRITE)
        )
        if not trusted_principal_source or not may_assert_principal:
            principal_kind, principal_ref = None, None
        ingested_by = _parse_ingest_principal(kind=principal_kind, ref=principal_ref)
        # An old structural IngestPort has no `ingested_by` keyword; passing it
        # unconditionally would break an unattributed call that used to work.
        attribution = {} if ingested_by is None else {"ingested_by": ingested_by}
        for name, instant in (
            ("source_modified_at", source_modified_at),
            ("effective_from", effective_from),
            ("effective_until", effective_until),
        ):
            if instant is not None and (
                instant.tzinfo is None or instant.utcoffset() != timedelta(0)
            ):
                raise HTTPException(
                    status_code=422, detail=f"{name} must be timezone-aware UTC"
                )
        if effective_until is not None and (
            effective_from is None or effective_until <= effective_from
        ):
            raise HTTPException(
                status_code=422,
                detail="effective_until requires an earlier effective_from",
            )
        if effective_from is not None and versioning_mode != "snapshot":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "effective_time_requires_snapshot",
                    "message": "effective periods require versioning_mode=snapshot",
                },
            )
        upload = DocumentUpload(
            filename=filename,
            mime=mime,
            content=content,
            title=title,
            source_path=source_path,
            version_key=version_key,
            effective_from=effective_from,
            effective_until=effective_until,
            tags=tuple(tags or ()),
        )
        if source_kind is None or source_ref is None:
            if (
                source_modified_at is not None
                or source_version_ref is not None
                or versioning_mode != "snapshot"
                or version_key is not None
                or effective_from is not None
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "source timestamps, revisions, living mode, version keys"
                        " and effective periods require source_kind/source_ref"
                    ),
                )
            try:
                return ingest.ingest(
                    deployment_id=deployment_id, upload=upload, **attribution
                )
            except ContentDetectionError as error:
                raise HTTPException(status_code=422, detail=error.code) from error
            except ForgottenSourceError as error:
                raise _forgotten_source_http_error() from error
            except (UnknownTagError, TagLimitError) as error:
                raise _tag_errors(error) from error
        try:
            return ingest.ingest_observed(
                deployment_id=deployment_id,
                source_kind=source_kind,
                source_ref=source_ref,
                upload=upload,
                versioning_mode=versioning_mode,
                source_modified_at=source_modified_at,
                source_version_ref=source_version_ref,
                sync_cycle_id=None,
                **attribution,
            )
        except ContentDetectionError as error:
            raise HTTPException(status_code=422, detail=error.code) from error
        except ForgottenSourceError as error:
            raise _forgotten_source_http_error() from error
        except (
            EffectivePeriodConflictError,
            VersionKeyConflictError,
            EffectiveTimeNotSupportedError,
        ) as error:
            raise _effective_time_errors(error) from error
        except (UnknownTagError, TagLimitError) as error:
            raise _tag_errors(error) from error


def _mount_connectors(*, app: FastAPI) -> None:
    """Add remote connector-management endpoints; execution stays server-side."""

    @app.get("/connectors", response_model=list[ConnectorDescriptor])
    def list_connectors(deployment: _Deployment) -> list[ConnectorDescriptor]:
        return list(
            _served(deployment.connectors).connectors(
                deployment_id=deployment.deployment_id
            )
        )

    @app.post("/connectors", response_model=ConnectorDescriptor)
    def add_connector(
        deployment: _Deployment, connector: ConnectorCreate
    ) -> ConnectorDescriptor:
        return _served(deployment.connectors).add(
            deployment_id=deployment.deployment_id, connector=connector
        )

    @app.post("/connectors/{connector_id}/pause", response_model=ConnectorDescriptor)
    def pause_connector(
        deployment: _Deployment, connector_id: UUID
    ) -> ConnectorDescriptor:
        try:
            return _served(deployment.connectors).pause(
                deployment_id=deployment.deployment_id, connector_id=connector_id
            )
        except ConnectorNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/connectors/{connector_id}", response_model=ConnectorDescriptor)
    def connector_status(
        deployment: _Deployment, connector_id: UUID
    ) -> ConnectorDescriptor:
        try:
            return _served(deployment.connectors).status(
                deployment_id=deployment.deployment_id, connector_id=connector_id
            )
        except ConnectorNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


def _authenticate(
    *, auth: AuthPerimeterPort, deployment_id: UUID, authorization: str | None
) -> AuthenticatedContext:
    """Authenticate the perimeter credential, or raise a 401/403.

    The `Authorization: <scheme> <value>` header is handed to the configured
    port; a failure, a missing header, or a credential for another deployment
    is refused before routing. This is the single enforcement point
    (retrieval §9) — inside, it is one trust domain.
    """
    if not authorization:
        raise HTTPException(
            status_code=401, detail="a perimeter credential is required"
        )
    scheme, _, value = authorization.partition(" ")
    try:
        context = auth.authenticate(
            credential=PerimeterCredential(
                scheme=scheme, value=SecretBytes(value.encode("utf-8"))
            )
        )
    except Exception as error:  # any auth failure is an opaque 401
        raise HTTPException(
            status_code=401, detail="perimeter authentication failed"
        ) from error
    if context.deployment_id != deployment_id:
        raise HTTPException(
            status_code=403, detail="credential is for another deployment"
        )
    return context


def _is_healthz(*, method: str, path: str) -> bool:
    """``GET /healthz``, the Compose liveness probe: exempt from the perimeter."""
    return method == "GET" and path.rstrip("/") == "/healthz"


def _perimeter(
    request: Request, deployment: _Deployment
) -> AuthenticatedContext | None:
    """Check the authenticated credential's scope for the matched route.

    :class:`_PerimeterGate` has already authenticated the request before
    routing and published its context; this runs per matched route because
    the scope a route needs is a property of the route. ``None`` when the
    request's deployment has no perimeter.
    """
    if deployment.auth is None:
        return None
    context: AuthenticatedContext | None = getattr(
        request.state, "perimeter_context", None
    )
    if context is None:
        if _is_healthz(method=request.method, path=get_route_path(request.scope)):
            return AuthenticatedContext(
                deployment_id=deployment.deployment_id, principal="healthz"
            )
        # The gate authenticates whenever the deployment has `auth`; reaching
        # here without a context is a wiring fault, refused rather than let
        # through.
        raise HTTPException(
            status_code=401, detail="a perimeter credential is required"
        )

    # Authentication answered "who"; this answers "may they". It lives here
    # rather than in the port because the port is handed a credential and
    # never sees the request — and because the method alone cannot tell a
    # read from a write on this API (`POST /graph/path` reads,
    # `POST /ingest` does not). See ``route_scope``.
    # The router's own path: relative to any mount and decoded, so a mounted
    # app classifies its routes exactly as an unmounted one.
    required = required_scope(method=request.method, path=get_route_path(request.scope))
    # D147: a host route the app was built to open to ingest credentials,
    # matched by the route FastAPI selected (its method and template), so a
    # listed template never covers another handler. Only routes the table
    # leaves at WRITE can be opened this way.
    route = request.scope.get("route")
    if (
        required is PerimeterScope.WRITE
        and isinstance(route, APIRoute)
        and (request.method.upper(), route.path)
        in request.app.state.ingest_scope_routes
    ):
        required = PerimeterScope.INGEST
    # ``None`` means the route decides for itself, because its authority is
    # a property of registry data rather than of the path. Today that is
    # only ``POST /operations/{name}``, whose handler asks the descriptor.
    if required is not None and not context.may(required=required):
        raise HTTPException(
            status_code=403, detail="credential may not perform this operation"
        )
    return context


def _open_query_principal(
    context: Annotated[AuthenticatedContext | None, Depends(_perimeter)],
) -> str | None:
    """The perimeter principal for execution-bearing open routes, or None.

    Reuses the app-level ``_perimeter`` result. Without a perimeter the
    facade keeps its default principal — no invented identity.
    """
    return None if context is None else context.principal


async def _announce_new_work(deployment: _Deployment) -> AsyncIterator[None]:
    """Announce the work rows a request created through its deployment's queue.

    Async, so the collector it opens is in the request's context, which the
    route's worker thread copies; the announcements run after the route has
    returned and its writes have committed (D147).
    """
    queue = deployment.task_queue
    if queue is None:
        yield
        return
    with collect_new_work() as created:
        yield
    if created:
        await run_in_threadpool(announce_new_work, queue=queue, work=tuple(created))


def _admission(deployment: _Deployment) -> None:
    """The request's deployment-wide D74 barrier, as one stable HTTP negative."""
    try:
        deployment.admission.assert_available(deployment_id=deployment.deployment_id)
    except ForgetInProgressError as error:
        raise HTTPException(
            status_code=503, detail={"code": "forget_in_progress"}
        ) from error


_ADMISSION_MESSAGES: Final = {
    "rate_limited": "request rate limit reached; retry after Retry-After seconds",
    "concurrency_limited": "too many requests in flight; retry after Retry-After seconds",
}

#: The admission hold of the request being served. Context variables are
#: copied into the worker thread that runs a synchronous handler, so the
#: handler wrapper finds the hold of the request that started it.
_CURRENT_HOLD: ContextVar[RequestHold | None] = ContextVar(
    "rememberstack_admission_hold", default=None
)


#: ASCII control characters, refused anywhere in a decoded request path.
_CONTROL_CHARACTER: Final = re.compile(r"[\x00-\x1f\x7f]")


class _PerimeterGate:
    """Resolve, authenticate and admit every request before routing (D136 §7.6).

    Pure ASGI and installed outside the spend lease, so the order is the
    request's deployment (:func:`deployment_context`, D147), authentication,
    admission, the spend hold, then routing. Running before routing means an
    unknown path is authenticated and counted like any other. ``GET /healthz``
    passes untouched. Without ``auth`` (no perimeter) only admission runs,
    against the deployment limits; the shared secret likewise has no
    credential id and meets the deployment limits only. Each check is the
    request's deployment's, so one app can serve several deployments.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the inner ASGI app."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse, or run the request holding its admission slot."""
        if scope["type"] == "http" and _CONTROL_CHARACTER.search(scope["path"]):
            # A decoded control character has no place in any route, and a
            # final newline even matches a route pattern's ``$``, so the
            # route and the spend classification could see different paths.
            await _send_json_error(send=send, status=400, detail="invalid_path")
            return
        if scope["type"] != "http" or _is_healthz(
            method=scope["method"], path=get_route_path(scope)
        ):
            await self._app(scope, receive, send)
            return
        context: AuthenticatedContext | None = None
        try:
            deployment = await _resolve_deployment(scope)
            # `request.state` reads this dict: routes, the spend lease, the
            # scope dependency and the ingest attribution check find the
            # deployment and the perimeter context there.
            state = scope.setdefault("state", {})
            state["api_deployment"] = deployment
            if deployment.auth is not None:
                authorization = Headers(scope=scope).get("authorization")
                # The port may do I/O (a key or revocation lookup); keep it off
                # the event loop, as the dependency it replaces was.
                context = await run_in_threadpool(
                    _authenticate,
                    auth=deployment.auth,
                    deployment_id=deployment.deployment_id,
                    authorization=authorization,
                )
                state["perimeter_context"] = context
            admission = deployment.direct_admission
            if admission is None:
                await self._app(scope, receive, send)
                return
            slot = admission.admit(key=admission_key(context))
        except HTTPException as error:
            refusal = JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers=error.headers,
            )
            await refusal(scope, receive, send)
            return
        except AdmissionRefused as error:
            refusal = JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": error.code,
                        "message": _ADMISSION_MESSAGES[error.code],
                    }
                },
                headers={"Retry-After": str(error.retry_after)},
            )
            await refusal(scope, receive, send)
            return
        hold = RequestHold(admission=admission, slot=slot)
        token = _CURRENT_HOLD.set(hold)
        try:
            await self._app(scope, receive, send)
        finally:
            _CURRENT_HOLD.reset(token)
            hold.request_finished()


class _HeldRoute(APIRoute):
    """A route whose synchronous work keeps its request's admission slot.

    FastAPI runs every ``def`` handler and ``def`` dependency (the D74
    barrier's database check among them) on a worker thread. A disconnect
    cancels the request's coroutine but not the thread, which goes on
    working; each such callable is wrapped so the request's
    :class:`RequestHold` sees the thread start and end, and the slot is
    released only when the work really stops. Coroutines are cancelled with
    their request, and generator dependencies are left as they are, so both
    need nothing. FastAPI finds dependency overrides by the original callable;
    :class:`_HeldOverrides` keeps them found (and held) after wrapping.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        """Build the route, then hold every synchronous callable it runs."""
        overrides = _HeldOverrides(provider=kwargs.get("dependency_overrides_provider"))
        kwargs["dependency_overrides_provider"] = overrides
        super().__init__(path, endpoint, **kwargs)
        # One wrapper per callable, so a dependency used twice keeps one
        # identity and FastAPI's per-request dependency cache still applies.
        wrappers: dict[int, Callable[..., Any]] = {}
        pending = [self.dependant]
        while pending:
            dependant = pending.pop()
            pending.extend(dependant.dependencies)
            call = dependant.call
            if (
                call is None
                or inspect.isclass(call)
                or dependant.is_coroutine_callable
                or dependant.is_gen_callable
                or dependant.is_async_gen_callable
            ):
                continue
            wrapper = wrappers.get(id(call))
            if wrapper is None:
                wrapper = wrappers[id(call)] = _held_handler(call)
                overrides.originals[wrapper] = call
            # The wrapper is sync like the original (and FastAPI inspects the
            # unwrapped callable), so it is still run on a worker thread.
            dependant.call = wrapper


class _HeldOverrides:
    """The app's dependency overrides, also keyed by a held route's wrappers.

    FastAPI looks an override up by the dependency's current callable, which a
    held route has replaced with its wrapper. This provider answers that
    lookup with the override registered for the original callable, itself
    held when it is synchronous, so an override is never silently skipped.
    """

    def __init__(self, *, provider: Any | None) -> None:
        """Wrap the app (or router) that owns ``dependency_overrides``."""
        self._provider = provider
        self.originals: dict[Callable[..., Any], Callable[..., Any]] = {}
        self._held: dict[int, Callable[..., Any]] = {}

    @property
    def dependency_overrides(self) -> dict[Callable[..., Any], Callable[..., Any]]:
        """The overrides, plus the same overrides under each wrapper."""
        overrides: dict[Callable[..., Any], Callable[..., Any]] = getattr(
            self._provider, "dependency_overrides", {}
        )
        if not overrides:
            return overrides
        result = dict(overrides)
        for wrapper, original in self.originals.items():
            override = overrides.get(original)
            if override is not None:
                result[wrapper] = self._held_override(override)
        return result

    def _held_override(self, override: Callable[..., Any]) -> Callable[..., Any]:
        """Hold a synchronous plain-function override like the callable it replaces."""
        if (
            inspect.isclass(override)
            or inspect.iscoroutinefunction(override)
            or inspect.isgeneratorfunction(override)
            or inspect.isasyncgenfunction(override)
        ):
            return override
        held = self._held.get(id(override))
        if held is None:
            held = self._held[id(override)] = _held_handler(override)
        return held


def _held_handler(call: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a synchronous handler or dependency so its request's hold sees it run."""

    @functools.wraps(call)
    def held(*args: Any, **kwargs: Any) -> Any:
        hold = _CURRENT_HOLD.get()
        if hold is None:
            return call(*args, **kwargs)
        hold.handler_started()
        try:
            return call(*args, **kwargs)
        finally:
            hold.handler_finished()

    return held


# D46/D109 reads. Every route that reads memory takes a lease, so a parked or
# unfunded deployment answers none of them; each commit reports the embedding
# cost the read incurred (0 for pure SQL or inventory reads). Service status
# (`/readiness`, `/deployment`, `/healthz`), the tool catalogue, connector
# management and deletion are not memory reads and stay outside the lease.
_READ_ROUTES: Final = frozenset(
    {
        ("GET", "/search/claims"),
        ("GET", "/search/chunks"),
        ("POST", "/search/claims"),
        ("POST", "/search/chunks"),
        ("POST", "/chunks/adjacent"),
        ("GET", "/resolve"),
        ("GET", "/lookup/relations"),
        ("GET", "/lookup/observations"),
        ("POST", "/graph/neighborhood"),
        ("POST", "/graph/path"),
        ("POST", "/graph/citation-path"),
        ("POST", "/query/sql"),
        ("POST", "/query/sql/explain"),
        ("GET", "/query/space"),
        ("GET", "/query/space/search"),
        ("GET", "/query/saved"),
        ("GET", "/documents"),
        ("POST", "/documents/search"),
        ("POST", "/documents/references"),
    }
)


def _spend_gated_route(*, method: str, path: str) -> tuple[str, str | None] | None:
    """Return ``(path_id, operation_name)`` for D46 spend-gated engine routes."""
    normalized = path.rstrip("/") or "/"
    if method == "POST" and normalized == "/ingest":
        return ("ingest", None)
    if (method, normalized) in _READ_ROUTES:
        return ("search", None)
    parts = normalized.split("/")[1:]
    if method == "POST" and len(parts) == 2 and parts[0] == "operations" and parts[1]:
        return ("recipe", parts[1])
    if (
        method == "GET"
        and len(parts) >= 5
        and parts[0] == "documents"
        and parts[2] == "sections"
        and parts[-1] == "history"
    ):
        # /documents/{doc_id}/sections/{section_key}/history. The key is a
        # path parameter, so it may hold '/' and empty segments ('a//b')
        # and still route: it is matched before empty segments are refused.
        return ("search", None)
    if not all(parts):
        # Every other route's parameters are single non-empty segments, so a
        # path with an empty segment matches no route (404) and spends nothing.
        return None
    if (
        method == "GET"
        and len(parts) == 5
        and parts[0] == "documents"
        and parts[2] == "versions"
        and parts[4] == "references"
    ):
        # /documents/{doc_id}/versions/{version_id}/references (D140)
        return ("search", None)
    if method == "GET" and len(parts) == 3:
        # /chunks/{id}/adjacent, /hydrate/relation/{id}, /transcript/relation/{id}
        if parts[0] == "chunks" and parts[2] == "adjacent":
            return ("search", None)
        if parts[0] in {"hydrate", "transcript"} and parts[1] == "relation":
            return ("search", None)
    # /query/saved/{namespace}/{name} and its /run
    saved = parts[:2] == ["query", "saved"]
    if method == "GET" and len(parts) == 4 and saved:
        return ("search", None)
    if method == "POST" and len(parts) == 5 and saved and parts[4] == "run":
        return ("search", None)
    return None


def _content_length(*, request: Request) -> int | None:
    """Parse Content-Length without reading the body."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _install_spend_lease(*, app: FastAPI) -> None:
    """Reserve metadata on spend-gated routes; commit 2xx, release otherwise.

    The lease is the request's deployment's (resolved by the gate outside);
    a deployment without one passes straight through.
    """

    @app.middleware("http")
    async def spend_lease_middleware(request: Request, call_next: Any) -> Any:
        spend_lease = _request_deployment(request).spend_lease
        if spend_lease is None:
            return await call_next(request)
        # The path the router matches: decoded, and relative to any mount
        # (``root_path``). ``request.url.path`` is neither, so a mounted app or
        # an encoded ``?``/``#`` inside a parameter would read ungated.
        gated = _spend_gated_route(
            method=request.method, path=get_route_path(request.scope)
        )
        if gated is None:
            return await call_next(request)
        authorization = request.headers.get("authorization")
        if not authorization:
            return await call_next(request)
        path_id, operation_name = gated
        try:
            reservation_id = spend_lease.reserve(
                authorization=authorization,
                path_id=path_id,
                content_length=_content_length(request=request),
                mime=request.query_params.get("mime"),
                operation_name=operation_name,
            )
        except SpendLeaseRefused as error:
            return JSONResponse(
                status_code=error.status_code, content={"detail": error.detail}
            )
        except SpendLeaseUnavailable:
            return JSONResponse(
                status_code=503, content={"detail": "spend_lease_unavailable"}
            )
        try:
            with track_read_embedding_cost() as read_cost:
                response = await call_next(request)
        except Exception:
            spend_lease.release(
                authorization=authorization, reservation_id=reservation_id
            )
            raise
        if 200 <= response.status_code < 300:
            try:
                spend_lease.commit(
                    authorization=authorization,
                    reservation_id=reservation_id,
                    read_cost=None if path_id == "ingest" else read_cost,
                )
            except (SpendLeaseRefused, SpendLeaseUnavailable):
                pass
        else:
            try:
                spend_lease.release(
                    authorization=authorization, reservation_id=reservation_id
                )
            except (SpendLeaseRefused, SpendLeaseUnavailable):
                pass
        return response
