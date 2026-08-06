"""The HTTP API surface (WP-1.6/WP-5.4): primitives and recipes over FastAPI.

A thin, typed veneer: every endpoint delegates to one QueryEngine primitive or
runs one recipe through the shared `RecipeSurface`, and returns the D49
envelope verbatim. The recipe endpoints render from the registry — `/recipes`
IS the registry's active rows — so the CLI and MCP surfaces stay in lockstep
by construction (they render the same surface).

The API is the one place authorization is enforced for query-engine reads
(retrieval §9): a deployment that passes an `AuthPerimeterPort` gets every
endpoint gated on a valid perimeter credential for THIS deployment — a single
trust domain, never per-request tenancy. With no port, the perimeter is
infrastructure's job and the app is open (the self-host default). The surface
itself never touches adapters.
"""

from datetime import datetime
from datetime import timedelta
from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal
from typing import Protocol
from uuid import UUID

from fastapi import Body
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretBytes

from rememberstack.model import AuthenticatedContext
from rememberstack.model import ConnectorCreate
from rememberstack.model import ConnectorDescriptor
from rememberstack.model import ConnectorNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import Envelope
from rememberstack.model import ForgetInProgressError
from rememberstack.model import IngestedVersion
from rememberstack.model import PerimeterCredential
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ToolDescriptor
from rememberstack.ports.auth import AuthPerimeterPort
from rememberstack.surfaces.query_engine import QueryEngine
from rememberstack.surfaces.query_engine import RESOLVE_CONTEXT_LIMIT
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.recipe_surface import InvalidArgumentError
from rememberstack.surfaces.recipe_surface import MissingArgumentError
from rememberstack.surfaces.recipe_surface import RecipeSurface
from rememberstack.surfaces.recipe_surface import UnknownRecipeError

PIPELINE_READINESS_VERSION_LIMIT: Final = 1_000
"""Maximum document versions in one read-only readiness inspection."""


class IngestPort(Protocol):
    """The E0 ingest operations the HTTP surface may expose."""

    def ingest(
        self, *, deployment_id: UUID, upload: DocumentUpload
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
    ) -> IngestedVersion: ...


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
    """Inspect ordinary per-version and aggregate projection completion."""

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require_projections: bool,
    ) -> PipelineReadinessReport: ...


class SqlQueryRequest(BaseModel):
    """Body for `POST /query/sql` and `POST /query/sql/explain`."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    parameters: list[Any] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=0)


class CypherQueryRequest(BaseModel):
    """Body for `POST /query/cypher` and `POST /query/cypher/explain`."""

    model_config = ConfigDict(extra="forbid")

    cypher: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    max_rows: int | None = Field(default=None, ge=0)
    confirm: bool = False


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
    surface: RecipeSurface | None = None,
    open_query: OpenQueryFacade | None = None,
    auth: AuthPerimeterPort | None = None,
    ingest: IngestPort | None = None,
    connectors: ConnectorManagementPort | None = None,
    pipeline_readiness: PipelineReadinessPort | None = None,
) -> FastAPI:
    """Build one deployment's query API over a composed engine.

    `surface` adds registry-rendered recipes; `open_query` adds the §3.1 open
    query routes; `ingest` exposes the E0 write gate; `connectors` manages
    deployment-side connector configuration; and `auth` gates every endpoint
    on one perimeter credential. Each capability is explicitly composed;
    absent services do not pretend to exist.
    """
    if surface is not None and surface.deployment_id != deployment_id:
        raise ValueError(
            "the recipe surface and the API serve different deployments —"
            " one deployment is one trust domain (D50)"
        )
    if open_query is not None and open_query.deployment_id != deployment_id:
        raise ValueError(
            "the open-query facade and the API serve different deployments —"
            " one deployment is one trust domain (D50)"
        )
    readiness.ensure_ready(deployment_id=deployment_id)
    dependencies = [
        *(
            [Depends(_perimeter(auth=auth, deployment_id=deployment_id))]
            if auth is not None
            else []
        ),
        Depends(_admission(admission=admission, deployment_id=deployment_id)),
    ]
    app = FastAPI(
        title="RememberStack query API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # a machine API; the schema endpoint is not gated, so off
        dependencies=dependencies,
    )

    @app.get("/resolve", response_model=Envelope)
    def resolve(
        name: str,
        entity_type: str | None = None,
        context_entity_ids: Annotated[
            list[UUID] | None, Query(max_length=RESOLVE_CONTEXT_LIMIT)
        ] = None,
    ) -> Envelope:
        """Resolve current entities, optionally ranked by focal context (S51)."""
        return engine.resolve(
            deployment_id=deployment_id,
            name=name,
            entity_type=entity_type,
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
        _mount_recipes(app=app, surface=surface)
    if open_query is not None:
        _mount_open_query(app=app, open_query=open_query)
    if ingest is not None:
        _mount_ingest(app=app, ingest=ingest, deployment_id=deployment_id)
    if connectors is not None:
        _mount_connectors(app=app, connectors=connectors, deployment_id=deployment_id)
    if pipeline_readiness is not None:
        _mount_pipeline_readiness(
            app=app, readiness=pipeline_readiness, deployment_id=deployment_id
        )

    return app


def _mount_open_query(*, app: FastAPI, open_query: OpenQueryFacade) -> None:
    """Add the nine §3.1 open-query routes when the facade is composed.

    Paths are short and consistent under `/query/…`. Legacy `/recipes` and
    `/recipe/{name}` are untouched. Sandbox and registry failures map to typed
    HTTP status + public error code without private engine detail.
    """

    @app.post("/query/sql", response_model=QueryResult)
    def query_sql(body: SqlQueryRequest) -> QueryResult:
        """One sandboxed SQL statement; QueryResult/v1."""
        return _open_call(
            lambda: open_query.query_sql(
                sql=body.sql, parameters=body.parameters, max_rows=body.max_rows
            )
        )

    @app.post("/query/sql/explain", response_model=QueryResult)
    def explain_sql(body: SqlQueryRequest) -> QueryResult:
        """EXPLAIN one SQL statement without executing it."""
        return _open_call(
            lambda: open_query.explain_sql(sql=body.sql, parameters=body.parameters)
        )

    @app.post("/query/cypher", response_model=QueryResult)
    def query_cypher(body: CypherQueryRequest) -> QueryResult:
        """One read-only Cypher statement over the published snapshot."""
        return _open_call(
            lambda: open_query.query_cypher(
                cypher=body.cypher,
                parameters=body.parameters,
                max_rows=body.max_rows,
                confirm=body.confirm,
            )
        )

    @app.post("/query/cypher/explain", response_model=QueryResult)
    def explain_cypher(body: CypherQueryRequest) -> QueryResult:
        """Engine plan for one Cypher statement without executing it."""
        return _open_call(
            lambda: open_query.explain_cypher(
                cypher=body.cypher, parameters=body.parameters
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
        namespace: str, name: str, body: RunSavedQueryRequest
    ) -> QueryResult:
        """Execute one active saved query through the same SQL executor."""
        return _open_call(
            lambda: open_query.run_saved_query(
                namespace=namespace,
                name=name,
                version=body.version,
                parameters=body.parameters,
                max_rows=body.max_rows,
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
    if code in (QueryErrorCode.SAVED_QUERY_NOT_FOUND, QueryErrorCode.P2_UNAVAILABLE):
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
        QueryErrorCode.LANCE_UNAVAILABLE,
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
        version_ids: Annotated[
            list[UUID], Body(min_length=1, max_length=PIPELINE_READINESS_VERSION_LIMIT)
        ],
        require_projections: bool = True,
    ) -> PipelineReadinessReport:
        """Inspect exact E generations and optionally fresh P2/P3 snapshots."""
        return readiness.inspect(
            deployment_id=deployment_id,
            version_ids=tuple(version_ids),
            require_projections=require_projections,
        )


def _mount_recipes(*, app: FastAPI, surface: RecipeSurface) -> None:
    """Add the registry-rendered recipe endpoints to the app (D50)."""

    @app.get("/recipes", response_model=list[ToolDescriptor])
    def list_recipes() -> list[ToolDescriptor]:
        """The recipe tool list — this deployment's active registry rows."""
        return list(surface.descriptors())

    @app.post("/recipe/{name}", response_model=Envelope)
    def run_recipe(
        name: str, arguments: Annotated[dict[str, object], Body(default_factory=dict)]
    ) -> Envelope:
        """Run one recipe by name over JSON arguments (the D50 executor)."""
        try:
            return surface.run(name=name, arguments=arguments)
        except UnknownRecipeError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (MissingArgumentError, InvalidArgumentError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


def _mount_ingest(*, app: FastAPI, ingest: IngestPort, deployment_id: UUID) -> None:
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
    ) -> IngestedVersion:
        """Push one file through E0, optionally as a stable lineage version."""
        if (source_kind is None) != (source_ref is None):
            raise HTTPException(
                status_code=422,
                detail="source_kind and source_ref must be supplied together",
            )
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
            return ingest.ingest(deployment_id=deployment_id, upload=upload)
        return ingest.ingest_observed(
            deployment_id=deployment_id,
            source_kind=source_kind,
            source_ref=source_ref,
            upload=upload,
            versioning_mode=versioning_mode,
            source_modified_at=source_modified_at,
            source_version_ref=source_version_ref,
            sync_cycle_id=None,
        )


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
    is a 401/403 before any read runs. This is the single enforcement point
    (retrieval §9) — inside, it is one trust domain.
    """

    def dependency(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedContext:
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
