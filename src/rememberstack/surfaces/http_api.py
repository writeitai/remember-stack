"""The HTTP API surface: primitives and assured operations over FastAPI.

A thin, typed veneer: every endpoint delegates to one QueryEngine primitive or
runs one operation through the shared ``OperationSurface``. ``/operations``
is the registry's active rows, so CLI and MCP stay in lockstep by construction.

The API is the one place authorization is enforced for query-engine reads
(retrieval §9): a deployment that passes an `AuthPerimeterPort` gets every
endpoint gated on a valid perimeter credential for THIS deployment — a single
trust domain, never per-request tenancy. With no port, the perimeter is
infrastructure's job and the app is open (the self-host default). The surface
itself never touches adapters.
"""

from datetime import datetime
from datetime import timedelta
from ipaddress import AddressValueError
from ipaddress import IPv6Address
import json
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
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import SecretBytes

from rememberstack.model import AuthenticatedContext
from rememberstack.model import ConnectorCreate
from rememberstack.model import ConnectorDescriptor
from rememberstack.model import ConnectorNotFoundError
from rememberstack.model import ContextBundleV1
from rememberstack.model import DocumentUpload
from rememberstack.model import Envelope
from rememberstack.model import ForgetInProgressError
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import IngestPrincipalKind
from rememberstack.model import ManagedTextClassificationError
from rememberstack.model import PerimeterCredential
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ProviderCallError
from rememberstack.model import ReadinessRequirements
from rememberstack.model import SpendLeaseRefused
from rememberstack.model import SpendLeaseUnavailable
from rememberstack.model import ToolDescriptor
from rememberstack.ports.auth import AuthPerimeterPort
from rememberstack.surfaces.graph_queries import GraphBusyError
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.operation_surface import InvalidArgumentError
from rememberstack.surfaces.operation_surface import MissingArgumentError
from rememberstack.surfaces.operation_surface import OperationSurface
from rememberstack.surfaces.operation_surface import UnknownOperationError
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

    def commit(self, *, authorization: str, reservation_id: UUID) -> None: ...

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
        limit: int = 500,
        continuation: str | None = None,
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
    valid_at: datetime | None = None
    believed_at: datetime | None = None
    limit: int = Field(default=500, ge=1, le=500)
    continuation: str | None = Field(default=None, max_length=200)
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
    valid_at: datetime | None = None
    believed_at: datetime | None = None

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
    max_rows: int | None = Field(default=None, ge=0)


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
    max_rows: int | None = Field(default=None, ge=0)


def build_api(
    *,
    engine: QueryEngine,
    deployment_id: UUID,
    admission: AdmissionPort,
    readiness: ReadinessPort,
    surface: OperationSurface | None = None,
    open_query: OpenQueryFacade | None = None,
    auth: AuthPerimeterPort | None = None,
    spend_lease: SpendLeasePort | None = None,
    ingest: IngestPort | None = None,
    connectors: ConnectorManagementPort | None = None,
    pipeline_readiness: PipelineReadinessPort | None = None,
    graph: GraphQueryPort | None = None,
    ingest_body_max_bytes: int | None = None,
    trusted_principal_source: bool = False,
    browser_origins: tuple[str, ...] = (),
) -> FastAPI:
    """Build one deployment's query API over a composed engine.

    `surface` adds registry-rendered operations; `open_query` adds the §3.1 open
    query routes; `ingest` exposes the E0 write gate; `connectors` manages
    deployment-side connector configuration; `auth` gates every endpoint
    on one perimeter credential; and `spend_lease` holds estimate on the
    control plane for ingest/search/operations POST (D46). Each capability
    is explicitly composed; absent services do not pretend to exist.

    `trusted_principal_source` declares that this deployment's perimeter is
    reached only by a caller entitled to state who ingested a document (a
    managed control plane). It is **off by default**: the deployment-wide
    bearer identifies a deployment, not a caller, so elsewhere an asserted
    `X-Ingest-Principal-*` pair is **ignored, never rejected** — metadata
    must not be able to fail an otherwise valid ingest.

    `ingest_body_max_bytes` bounds `POST /ingest` request bodies before they
    are buffered (413 over the cap; 411 when no Content-Length is declared).
    None — the self-host default — imposes no limit: caps are deployment
    policy, never an engine default (D61).
    """
    if surface is not None and surface.deployment_id != deployment_id:
        raise ValueError(
            "the operation surface and the API serve different deployments —"
            " one deployment is one trust domain (D50)"
        )
    if open_query is not None and open_query.deployment_id != deployment_id:
        raise ValueError(
            "the open-query facade and the API serve different deployments —"
            " one deployment is one trust domain (D50)"
        )
    readiness.ensure_ready(deployment_id=deployment_id)
    # One perimeter dependency instance so app-level gating and open-query
    # principal injection share the same authenticate call per request.
    perimeter_dep = (
        _perimeter(auth=auth, deployment_id=deployment_id) if auth is not None else None
    )
    dependencies = [
        *([Depends(perimeter_dep)] if perimeter_dep is not None else []),
        Depends(_admission(admission=admission, deployment_id=deployment_id)),
    ]
    app = FastAPI(
        title="RememberStack query API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # a machine API; the schema endpoint is not gated, so off
        dependencies=dependencies,
    )
    _install_browser_origins(app=app, origins=browser_origins)

    @app.get("/resolve", response_model=Envelope)
    def resolve(
        name: str,
        context_entity_ids: Annotated[
            list[UUID] | None, Query(max_length=RESOLVE_CONTEXT_LIMIT)
        ] = None,
    ) -> Envelope:
        """Resolve current entities, optionally ranked by focal context (S51)."""
        return engine.resolve(
            deployment_id=deployment_id,
            name=name,
            context_entity_ids=tuple(context_entity_ids or ()),
        )

    @app.get("/lookup/relations", response_model=Envelope)
    def lookup_relations(
        subject_entity_id: UUID | None = None,
        predicate: str | None = None,
        object_entity_id: UUID | None = None,
        valid_at: datetime | None = None,
    ) -> Envelope:
        """Relations matching an (s, p, o) pattern — current, or as-of (S9)."""
        return engine.lookup_relations(
            deployment_id=deployment_id,
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            valid_at=valid_at,
        )

    @app.get("/transcript/relation/{relation_id}", response_model=Envelope)
    def transcript_relation(relation_id: UUID) -> Envelope:
        """The S8 audit query: why the system believes what it believes."""
        return engine.transcript_relation(
            deployment_id=deployment_id, relation_id=relation_id
        )

    @app.get("/lookup/observations", response_model=Envelope)
    def lookup_observations(
        entity_id: UUID, property_query: str | None = None, k: int = 10
    ) -> Envelope:
        """Live observations on one entity, semantic over statements (S2)."""
        return engine.lookup_observations(
            deployment_id=deployment_id,
            entity_id=entity_id,
            property_query=property_query,
            k=k,
        )

    @app.get("/search/claims", response_model=Envelope)
    def search_claims(
        query: str,
        k: Annotated[int, Query(ge=1, le=400)] = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Claim search — evidence grain, never current-fact truth."""
        return engine.search_claims(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )

    @app.get("/search/chunks", response_model=Envelope)
    def search_chunks(
        query: str,
        k: Annotated[int, Query(ge=1, le=400)] = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Search live source chunks as separately typed evidence."""
        return engine.search_chunks(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )

    @app.get("/hydrate/relation/{relation_id}", response_model=Envelope)
    def hydrate_relation(relation_id: UUID) -> Envelope:
        """The S5 chain: relation → evidence claims → source documents."""
        return engine.hydrate_relation(
            deployment_id=deployment_id, relation_id=relation_id
        )

    if surface is not None:
        _mount_operations(app=app, surface=surface)
    if open_query is not None:
        _mount_open_query(app=app, open_query=open_query, perimeter=perimeter_dep)
    if ingest is not None:
        _mount_ingest(
            app=app,
            ingest=ingest,
            deployment_id=deployment_id,
            max_body_bytes=ingest_body_max_bytes,
            trusted_principal_source=trusted_principal_source,
        )
        if ingest_body_max_bytes is not None:
            app.add_middleware(_IngestBodyLimit, max_bytes=ingest_body_max_bytes)
    if connectors is not None:
        _mount_connectors(app=app, connectors=connectors, deployment_id=deployment_id)
    if pipeline_readiness is not None:
        _mount_pipeline_readiness(
            app=app, readiness=pipeline_readiness, deployment_id=deployment_id
        )
    if graph is not None:
        _mount_graph(app=app, graph=graph)

    if spend_lease is not None:
        _install_spend_lease(app=app, spend_lease=spend_lease)

    return app


#: The 253 characters DNS allows a hostname.
_HOST_MAX_LENGTH = 253

#: One hostname label. Underscore is permitted because browsers do serialize
#: it, even though RFC 1123 does not; a hyphen may not lead or trail.
_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


def _canonical_browser_origin(value: str) -> str | None:
    """The origin a browser would send for ``value``, or ``None`` if there is none.

    Checking the *shape* of an origin with a pattern was the first attempt and
    it kept being wrong in both directions — it admitted `https://[::::]` and
    malformed punycode, while refusing hosts a browser will happily serialize.

    So this parses instead, rebuilds the origin the way a browser serializes
    one, and requires the result to equal the input. Anything non-canonical
    fails that comparison without needing to be enumerated: an uppercase host,
    a default port written out, a stray slash. What parsing cannot decide —
    whether the host is a *real* host rather than `*` or a broken IPv6 literal
    — is checked explicitly.
    """
    try:
        split = urlsplit(value)
    except ValueError:
        # `urlsplit` itself refuses a malformed bracketed host such as
        # `https://[::::]`, which is a verdict, not an error to propagate.
        return None
    if split.scheme != "https" or split.path or split.query or split.fragment:
        return None
    if split.username is not None or split.password is not None:
        return None
    try:
        host, port = split.hostname, split.port
    except ValueError:
        # `urlsplit` raises for a port outside 1-65535, which is exactly the
        # judgement wanted here.
        return None
    if not host:
        return None
    # `urlsplit` refuses a port above 65535 but accepts 0, which is not a port
    # anything can listen on and so cannot appear in a real origin.
    if port == 0:
        return None

    if value.startswith("https://["):
        try:
            IPv6Address(host)
        except AddressValueError:
            return None
        rendered = f"[{host}]"
    else:
        if len(host) > _HOST_MAX_LENGTH:
            return None
        labels = host.split(".")
        # A trailing dot is a legitimate absolute name, so one empty final
        # label is allowed; any other empty label is a typo like `a..b`.
        if labels and labels[-1] == "":
            labels = labels[:-1]
        if not labels or not all(_LABEL.match(label) for label in labels):
            return None
        for label in labels:
            if label.startswith("xn--"):
                try:
                    label.encode("ascii").decode("idna")
                except (UnicodeError, ValueError):
                    return None
        rendered = host

    return f"https://{rendered}" + (f":{port}" if port is not None else "")


def _install_browser_origins(*, app: FastAPI, origins: tuple[str, ...]) -> None:
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
                "browser origins must each be an https origin exactly as a "
                "browser serializes it — lowercase scheme and host, optional "
                f"port, nothing else; refusing {origin!r}"
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
        allow_methods=["GET", "POST"],
        # Exactly the two headers a browser client sends. `Idempotency-Key`
        # was here for a contract nothing implements — advertising a header no
        # route reads invites a client to rely on it.
        allow_headers=["Authorization", "Content-Type"],
        # Starlette's default. Worth knowing that removing an origin leaves a
        # browser's cached preflight usable for up to this long, so revoking
        # the credential is what stops access immediately, not this list.
        max_age=600,
    )


def _mount_graph(*, app: FastAPI, graph: GraphQueryPort) -> None:
    """Mount the three server-owned graph operations."""

    @app.post("/graph/neighborhood", response_model=Envelope)
    def graph_neighborhood(body: GraphNeighborhoodRequest) -> Envelope:
        """Return a current or bitemporal bounded entity neighborhood."""
        try:
            return graph.neighborhood(
                entity_id=body.entity_id,
                hops=body.hops,
                predicates=body.predicates,
                valid_at=body.valid_at,
                believed_at=body.believed_at,
                limit=body.limit,
                continuation=body.continuation,
                include_paths=body.include_paths,
            )
        except (GraphBusyError, GraphHydrationError) as error:
            detail = (
                "live graph is busy"
                if isinstance(error, GraphBusyError)
                else "live graph result unavailable"
            )
            raise HTTPException(status_code=503, detail=detail) from error

    @app.post("/graph/path", response_model=Envelope)
    def graph_path(body: GraphPathRequest) -> Envelope:
        """Return bounded equal-length shortest paths between two entities."""
        try:
            return graph.path(
                from_entity_id=body.from_entity_id,
                to_entity_id=body.to_entity_id,
                max_hops=body.max_hops,
                predicates=body.predicates,
                valid_at=body.valid_at,
                believed_at=body.believed_at,
            )
        except (GraphBusyError, GraphHydrationError) as error:
            detail = (
                "live graph is busy"
                if isinstance(error, GraphBusyError)
                else "live graph result unavailable"
            )
            raise HTTPException(status_code=503, detail=detail) from error

    @app.post("/graph/citation-path", response_model=Envelope)
    def graph_citation_path(body: GraphCitationPathRequest) -> Envelope:
        """Return bounded directed citation paths between two documents."""
        try:
            return graph.citation_path(
                from_doc_id=body.from_doc_id,
                to_doc_id=body.to_doc_id,
                max_hops=body.max_hops,
            )
        except (GraphBusyError, GraphHydrationError) as error:
            detail = (
                "live graph is busy"
                if isinstance(error, GraphBusyError)
                else "live graph result unavailable"
            )
            raise HTTPException(status_code=503, detail=detail) from error


def _mount_open_query(
    *, app: FastAPI, open_query: OpenQueryFacade, perimeter: Any | None = None
) -> None:
    """Add the seven §3.1 open-query routes when the facade is composed.

    Paths are short and consistent under `/query/…`. Sandbox and registry failures map to typed
    HTTP status + public error code without private engine detail.

    When a perimeter dependency is composed, every execution-bearing route
    forwards ``AuthenticatedContext.principal`` into the facade. Content-only
    discovery and metadata routes stay content-free and do not invent
    principals. Without auth, the facade default principal is unchanged.
    """
    principal_dep = _open_query_principal_dependency(perimeter=perimeter)

    @app.post("/query/sql", response_model=QueryResult)
    def query_sql(
        body: SqlQueryRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
    ) -> QueryResult:
        """One sandboxed SQL statement; QueryResult/v1."""
        return _open_call(
            lambda: open_query.query_sql(
                sql=body.sql,
                parameters=body.parameters,
                max_rows=body.max_rows,
                principal=principal,
            )
        )

    @app.post("/query/sql/explain", response_model=QueryResult)
    def explain_sql(
        body: SqlExplainRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
    ) -> QueryResult:
        """EXPLAIN one SQL statement without executing it."""
        return _open_call(
            lambda: open_query.explain_sql(
                sql=body.sql, parameters=body.parameters, principal=principal
            )
        )

    @app.get("/query/space")
    def describe_query_space(
        pattern: str | None = None, include_examples: bool = False
    ) -> dict[str, object]:
        """Manifest-backed schema discovery (content-free)."""
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
        query: Annotated[str, Query(min_length=1)],
        k: Annotated[int, Query(ge=1, le=25)] = 10,
    ) -> list[dict[str, object]]:
        """Search checked-in manifest text only."""
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
        namespace: str | None = None, status: str | None = None
    ) -> list[dict[str, object]]:
        """Registry metadata for discoverable saved queries."""
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
        namespace: str, name: str, version: int | None = None
    ) -> dict[str, object]:
        """One immutable saved-query version."""
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
        namespace: str,
        name: str,
        body: RunSavedQueryRequest,
        principal: Annotated[str | None, Depends(principal_dep)] = None,
    ) -> QueryResult:
        """Execute one active saved query through the same SQL executor."""
        return _open_call(
            lambda: open_query.run_saved_query(
                namespace=namespace,
                name=name,
                version=body.version,
                parameters=body.parameters,
                max_rows=body.max_rows,
                principal=principal,
            )
        )


def _open_query_principal_dependency(*, perimeter: Any | None) -> Any:
    """Build the FastAPI dependency that yields the execution principal.

    With a perimeter, reuses the same ``_perimeter`` dependency and returns
    ``AuthenticatedContext.principal``. Without auth, returns ``None`` so the
    facade keeps its default principal — no invented identity.
    """
    if perimeter is None:

        def anonymous_principal() -> None:
            """No auth perimeter; leave principal selection to the facade."""
            return None

        return anonymous_principal

    def authenticated_principal(
        ctx: Annotated[AuthenticatedContext, Depends(perimeter)],
    ) -> str:
        """Forward the perimeter principal into execution-bearing open routes."""
        return ctx.principal

    return authenticated_principal


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


def _mount_pipeline_readiness(
    *, app: FastAPI, readiness: PipelineReadinessPort, deployment_id: UUID
) -> None:
    """Expose a normal, read-only completion boundary for bounded versions."""

    @app.post("/readiness", response_model=PipelineReadinessReport)
    def pipeline_readiness(
        request: Annotated[PipelineReadinessRequest, Body()],
    ) -> PipelineReadinessReport:
        """Inspect exact pipeline and explicitly requested capabilities."""
        return readiness.inspect(
            deployment_id=deployment_id,
            version_ids=tuple(request.version_ids),
            require=request.require,
        )


def _mount_operations(*, app: FastAPI, surface: OperationSurface) -> None:
    """Add the registry-rendered assured-operation endpoints (D50/D87)."""

    @app.get("/operations", response_model=list[ToolDescriptor])
    def list_operations() -> list[ToolDescriptor]:
        """The four assured operations for this deployment."""
        return list(surface.descriptors())

    @app.post("/operations/{name}", response_model=Envelope | ContextBundleV1)
    def run_operation(
        name: str,
        arguments: Annotated[dict[str, object], Body(default_factory=dict)],
        request: Request,
    ) -> Envelope | ContextBundleV1:
        """Run one assured operation by name over JSON arguments.

        The route-level table cannot classify this one: operations are registry
        data, and whether a given operation mutates is a property of the
        operation rather than of the path. So the scope check happens here,
        against the descriptor, and an operation that has not declared itself
        non-mutating is refused to a read-only credential.
        """
        context = getattr(request.state, "perimeter_context", None)
        if context is not None:
            descriptor = next(
                (item for item in surface.descriptors() if item.name == name), None
            )
            required = operation_scope(
                mutates=descriptor.mutates if descriptor is not None else None
            )
            if not context.scope.covers(required=required):
                raise HTTPException(
                    status_code=403, detail="credential may not perform this operation"
                )
        try:
            return surface.run(name=name, arguments=arguments)
        except UnknownOperationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (MissingArgumentError, InvalidArgumentError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": str(error)},
            ) from error
        except ProviderCallError as error:
            raise HTTPException(
                status_code=503, detail="model provider unavailable"
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

    Only reached on a trusted perimeter, so a malformed pair here is a real
    client error worth reporting rather than metadata that should be dropped.
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


def _managed_text_http_error(*, error: ManagedTextClassificationError) -> HTTPException:
    """Map bounded pre-version classifier refusals to stable HTTP failures."""
    status = 413 if error.code == "source_bytes_limit_exceeded" else 422
    if error.code == "rate_class_unavailable":
        status = 409
    return HTTPException(status_code=status, detail=error.code)


def _mount_ingest(
    *,
    app: FastAPI,
    ingest: IngestPort,
    deployment_id: UUID,
    max_body_bytes: int | None,
    trusted_principal_source: bool = False,
) -> None:
    """Add the D62 lineage-aware push surface over the E0 ingest gate."""

    @app.post("/ingest", response_model=IngestedVersion)
    def ingest_document(
        content: Annotated[bytes, Body(media_type="application/octet-stream")],
        filename: Annotated[str, Query(min_length=1)],
        mime: Annotated[str, Query(min_length=1)],
        title: str | None = None,
        source_kind: Annotated[str | None, Query(min_length=1)] = None,
        source_ref: Annotated[str | None, Query(min_length=1)] = None,
        source_modified_at: datetime | None = None,
        versioning_mode: Literal["snapshot", "living"] = "snapshot",
        source_version_ref: str | None = None,
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
                    " declares a trusted principal source; malformed values"
                    " are 422 only on a trusted deployment."
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
        perimeter trusted (``trusted_principal_source``). The deployment-wide
        bearer identifies a deployment, not a caller, so elsewhere any client
        could assert it was a person. Untrusted attribution is **ignored, not
        rejected**: nothing forged is recorded either way, and refusing would
        let a metadata concern fail an otherwise valid ingest.
        """
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
        # An untrusted deployment must discard them without inspection: if
        # malformed attribution could 422, a metadata concern would still fail
        # an otherwise valid upload, which is exactly what ignoring is for.
        if not trusted_principal_source:
            principal_kind, principal_ref = None, None
        ingested_by = _parse_ingest_principal(kind=principal_kind, ref=principal_ref)
        # An old structural IngestPort has no `ingested_by` keyword; passing it
        # unconditionally would break an unattributed call that used to work.
        attribution = {} if ingested_by is None else {"ingested_by": ingested_by}
        if source_modified_at is not None and (
            source_modified_at.tzinfo is None
            or source_modified_at.utcoffset() != timedelta(0)
        ):
            raise HTTPException(
                status_code=422, detail="source_modified_at must be timezone-aware UTC"
            )
        upload = DocumentUpload(
            filename=filename, mime=mime, content=content, title=title
        )
        if source_kind is None or source_ref is None:
            if (
                source_modified_at is not None
                or source_version_ref is not None
                or versioning_mode != "snapshot"
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "source timestamps, revisions, and living mode require"
                        " source_kind/source_ref"
                    ),
                )
            try:
                return ingest.ingest(
                    deployment_id=deployment_id, upload=upload, **attribution
                )
            except ManagedTextClassificationError as error:
                raise _managed_text_http_error(error=error) from error
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
        except ManagedTextClassificationError as error:
            raise _managed_text_http_error(error=error) from error


def _mount_connectors(
    *, app: FastAPI, connectors: ConnectorManagementPort, deployment_id: UUID
) -> None:
    """Add remote connector-management endpoints; execution stays server-side."""

    @app.get("/connectors", response_model=list[ConnectorDescriptor])
    def list_connectors() -> list[ConnectorDescriptor]:
        return list(connectors.connectors(deployment_id=deployment_id))

    @app.post("/connectors", response_model=ConnectorDescriptor)
    def add_connector(connector: ConnectorCreate) -> ConnectorDescriptor:
        return connectors.add(deployment_id=deployment_id, connector=connector)

    @app.post("/connectors/{connector_id}/pause", response_model=ConnectorDescriptor)
    def pause_connector(connector_id: UUID) -> ConnectorDescriptor:
        try:
            return connectors.pause(
                deployment_id=deployment_id, connector_id=connector_id
            )
        except ConnectorNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/connectors/{connector_id}", response_model=ConnectorDescriptor)
    def connector_status(connector_id: UUID) -> ConnectorDescriptor:
        try:
            return connectors.status(
                deployment_id=deployment_id, connector_id=connector_id
            )
        except ConnectorNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


def _perimeter(*, auth: AuthPerimeterPort, deployment_id: UUID):  # noqa: ANN202
    """A FastAPI dependency that authenticates the perimeter credential.

    The `Authorization: <scheme> <value>` header is handed to the configured
    port; a failure, a missing header, or a credential for another deployment
    is a 401/403 before any read runs. ``GET /healthz`` is the Compose
    liveness probe and is the only path exempt from the Bearer check. This
    is the single enforcement point (retrieval §9) — inside, it is one trust
    domain.
    """

    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> AuthenticatedContext:
        if request.method == "GET" and request.url.path.rstrip("/") == "/healthz":
            return AuthenticatedContext(
                deployment_id=deployment_id, principal="healthz"
            )
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

        # Authentication answered "who"; this answers "may they". It lives here
        # rather than in the port because the port is handed a credential and
        # never sees the request — and because the method alone cannot tell a
        # read from a write on this API (`POST /graph/path` reads,
        # `POST /ingest` does not). See ``route_scope``.
        required = required_scope(method=request.method, path=request.url.path)
        # ``None`` means the route decides for itself, because its authority is
        # a property of registry data rather than of the path. Today that is
        # only ``POST /operations/{name}``, whose handler asks the descriptor.
        if required is not None and not context.scope.covers(required=required):
            raise HTTPException(
                status_code=403, detail="credential may not perform this operation"
            )
        # Published for the one route the table cannot classify statically:
        # ``POST /operations/{name}`` asks the descriptor instead.
        request.state.perimeter_context = context
        return context

    return dependency


def _admission(*, admission: AdmissionPort, deployment_id: UUID):  # noqa: ANN202
    """Return the deployment-wide D74 traffic dependency."""

    def dependency() -> None:
        """Map a closed fail-safe barrier to one stable HTTP negative."""
        try:
            admission.assert_available(deployment_id=deployment_id)
        except ForgetInProgressError as error:
            raise HTTPException(
                status_code=503, detail={"code": "forget_in_progress"}
            ) from error

    return dependency


def _spend_gated_route(*, method: str, path: str) -> tuple[str, str | None] | None:
    """Return ``(path_id, operation_name)`` for D46 spend-gated engine routes."""
    normalized = path.rstrip("/") or "/"
    if method == "POST" and normalized == "/ingest":
        return ("ingest", None)
    if method == "GET" and normalized in {"/search/claims", "/search/chunks"}:
        return ("search", None)
    if method == "POST" and normalized.startswith("/operations/"):
        name = normalized.removeprefix("/operations/")
        if name and "/" not in name:
            return ("recipe", name)
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


def _install_spend_lease(*, app: FastAPI, spend_lease: SpendLeasePort) -> None:
    """Reserve metadata on spend-gated routes; commit 2xx, release otherwise."""

    @app.middleware("http")
    async def spend_lease_middleware(request: Request, call_next: Any) -> Any:
        gated = _spend_gated_route(method=request.method, path=request.url.path)
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
            response = await call_next(request)
        except Exception:
            spend_lease.release(
                authorization=authorization, reservation_id=reservation_id
            )
            raise
        if 200 <= response.status_code < 300:
            try:
                spend_lease.commit(
                    authorization=authorization, reservation_id=reservation_id
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
