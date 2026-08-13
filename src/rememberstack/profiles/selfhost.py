"""Executable self-host composition for the WP-0.4c Compose quickstart."""

from __future__ import annotations

import argparse
from functools import partial
import json
from pathlib import Path
import sys
from typing import Self
from typing import TYPE_CHECKING
from uuid import UUID

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql as pg_sql
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalFSForgetManifestStore
from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost import MinIOSettings
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DeploymentBuildInfo
from rememberstack.model import EmbeddingRequest
from rememberstack.model import PipelineStage
from rememberstack.model import PublishedMounts
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine import AssuredOperationRegistry
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import seed_canonical_operations
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

if TYPE_CHECKING:
    from fastapi import FastAPI

    from rememberstack.adapters.selfhost import SelfHostWorkerLoop
    from rememberstack.ports.telemetry import TelemetryPort
    from rememberstack.workers import StageHandler

_SUPPORTED_WORKER_STAGES = (
    PipelineStage.CONVERT,
    PipelineStage.STRUCTURE,
    PipelineStage.CHUNK,
    PipelineStage.EMBED_CHUNK,
    PipelineStage.EXTRACT_CLAIMS,
    PipelineStage.NORMALIZE_RELATIONS,
    PipelineStage.ADJUDICATE_OBSERVATIONS,
    PipelineStage.ADJUDICATE_SUPERSESSION,
    PipelineStage.EMBED_CLAIM,
    PipelineStage.RECONCILE,
    PipelineStage.LABEL_RELATION,
)


class BuildProvenanceSettings(BaseSettings):
    """The source revision stamped into this image when it was built.

    A filesystem checkout proves nothing about what the containers run: Compose
    resolves a published image unless told to build, so a benchmark can record a
    commit that never produced its numbers. This value travels inside the image
    itself, which is why it can be trusted to answer "what code is serving".
    """

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    build_revision: str = ""


class SelfHostSettings(BaseSettings):
    """One fresh self-host deployment's profile and process settings."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_SELFHOST_", extra="ignore"
    )

    deployment_id: UUID
    deployment_slug: str = Field(default="local", min_length=1)
    deployment_name: str = Field(default="Local memory", min_length=1)
    default_language: str = Field(default="en", min_length=1)
    raw_bucket_name: str = Field(default="remember-raw", min_length=1)
    artifacts_bucket_name: str = Field(default="remember-artifacts", min_length=1)
    corpusfs_bucket_name: str = Field(default="remember-corpusfs", min_length=1)
    snapshot_bucket_name: str = Field(default="remember-snapshots", min_length=1)
    lance_root: Path = Path("/var/lib/rememberstack/lance")
    projection_work_root: Path = Path("/var/lib/rememberstack/projection-work")
    graph_cache_root: Path = Path("/var/lib/rememberstack/graph-cache")
    forget_manifest_root: Path = Path("/var/lib/rememberstack/forget-manifests")
    migration_config: Path = Path("alembic.ini")
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65_535)
    worker_rate_per_s: float = Field(default=20.0, gt=0)
    worker_burst: float = Field(default=20.0, ge=1)
    worker_fallback_poll_s: float = Field(default=5.0, gt=0)
    worker_session_s: float = Field(default=3_600.0, gt=0)


class SentrySettings(BaseSettings):
    """Strictly opt-in self-host error-tracking settings."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_SENTRY_", env_ignore_empty=True, extra="ignore"
    )

    dsn: SecretStr | None = None
    environment: str | None = None
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    def configured_dsn(self) -> str | None:
        """Return a non-empty DSN only when error tracking is explicitly enabled."""
        if self.dsn is None:
            return None
        value = self.dsn.get_secret_value().strip()
        return value or None


class _FreshDeploymentReadiness:
    """Fail closed if a fresh quickstart sees portable forget history.

    WP-0.4c establishes a fresh-deployment Compose skeleton. It must never
    silently serve a restored deployment whose D74 manifests require the full
    hard-forget recovery composition; finding any manifest stops startup.
    """

    def __init__(self, *, store: LocalFSForgetManifestStore) -> None:
        """Bind the separately durable manifest root."""
        self._store = store

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept an empty root and refuse every non-empty restore."""
        manifests = self._store.manifests(deployment_id=deployment_id)
        if manifests:
            raise RuntimeError(
                "the Compose quickstart found portable hard-forget manifests;"
                " restore requires the complete D74 self-host recovery profile"
            )
        return ()


class _SelfHostSavedQueryReads:
    """Per-call registry reads over a short-lived spine connection.

    Saved-query metadata lives outside the query role's grants. Each method
    opens an app connection, delegates to `SavedQueryRegistry`, and closes —
    so the open-query facade never holds a long-lived registry session.
    """

    def __init__(
        self, *, engine: Engine, deployment_id: UUID, manifest_hash: str
    ) -> None:
        self._engine = engine
        self._deployment_id = deployment_id
        self._manifest_hash = manifest_hash

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this read proxy serves."""
        return self._deployment_id

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ):
        """Delegate list discovery to a short-lived registry instance."""
        return self._with_registry(
            lambda registry: registry.list_saved_queries(
                namespace=namespace, status=status
            )
        )

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ):
        """Delegate describe to a short-lived registry instance."""
        return self._with_registry(
            lambda registry: registry.describe_saved_query(
                namespace=namespace, name=name, version=version
            )
        )

    def resolve(self, *, namespace: str, name: str, version: int | None = None):
        """Delegate active-version resolve to a short-lived registry instance."""
        return self._with_registry(
            lambda registry: registry.resolve(
                namespace=namespace, name=name, version=version
            )
        )

    def _with_registry(self, action):  # noqa: ANN001, ANN202
        """Open one short-lived registry, run ``action``, and close."""
        from rememberstack.surfaces.query_sandbox.saved_queries import (  # noqa: PLC0415
            PLATFORM_SEED_ACTOR,
        )
        from rememberstack.surfaces.query_sandbox.saved_queries import (  # noqa: PLC0415
            SavedQueryRegistry,
        )

        with self._engine.connect() as sa_connection:
            raw = sa_connection.connection.dbapi_connection
            assert isinstance(raw, psycopg.Connection)
            registry = SavedQueryRegistry(
                connection=raw,
                deployment_id=self._deployment_id,
                manifest_hash=self._manifest_hash,
                actor=PLATFORM_SEED_ACTOR,
            )
            return action(registry)


def _query_role_connect_factory(*, engine: Engine):
    """Bind a query-role connection factory to one profile engine URL.

    Uses psycopg keyword arguments so passwords containing ``@``, ``:``, or
    ``/`` cannot corrupt a reconstructed DSN. Derives host/port/database/
    password from the injected profile engine rather than reloading settings.
    """
    from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (  # noqa: PLC0415
        query_role_name,
    )

    url = engine.url
    database = url.database or ""
    role = query_role_name(database)
    password = url.password or ""
    host = url.host or "localhost"
    port = int(url.port or 5432)

    def connect() -> psycopg.Connection:
        """Open as the deployment query role with keyword connection args."""
        return psycopg.connect(
            host=host, port=port, dbname=database, user=role, password=password
        )

    return connect


def selfhost_embed_query(
    *,
    model_provider: ModelProviderPort,
    embedding_model: str,
    query: str,
    embedder_generation: str | None = None,
    surface_cost: SqlSurfaceCostRecorder | None = None,
    deployment_id: UUID | None = None,
) -> tuple[float, ...]:
    """Embed one query text with the same model the E1 path stamps.

    The configured E1 embedding model is the only generation this host can
    produce. A supplied generation that differs fails closed with
    ``generation_unavailable`` (D80); unpinned or current-model calls keep
    normal embed behavior.
    """
    if embedder_generation is not None and embedder_generation != embedding_model:
        raise SandboxRejection(
            code=QueryErrorCode.GENERATION_UNAVAILABLE,
            message="requested embedder_generation is not available on this host",
        )
    from rememberstack.model import ProviderCallError
    from rememberstack.spine.surface_cost import SurfaceCallSite
    from rememberstack.spine.surface_cost import SurfaceCostOutcome

    try:
        response = model_provider.embed(
            request=EmbeddingRequest(model=embedding_model, texts=(query,))
        )
    except ProviderCallError as error:
        if (
            error.usage is not None
            and surface_cost is not None
            and deployment_id is not None
        ):
            surface_cost.record(
                usage=error.usage,
                outcome=SurfaceCostOutcome.PROVIDER_ERROR,
                call_site=SurfaceCallSite.OPEN_QUERY_SQL,
                deployment_id=deployment_id,
            )
        raise
    if surface_cost is not None and deployment_id is not None:
        surface_cost.record(
            usage=response.usage,
            outcome=SurfaceCostOutcome.OK,
            call_site=SurfaceCallSite.OPEN_QUERY_SQL,
            deployment_id=deployment_id,
        )
    return tuple(response.vectors[0])


def _provision_query_role_password(
    *, connection: psycopg.Connection, engine: Engine
) -> None:
    """Deploy-time provisioning: set the query-role password from the engine URL.

    Runs only from ``SelfHostProfile.setup()``, never during API request or
    startup composition. Migrations create a LOGIN NOINHERIT role without a
    password; self-host reuses the spine password so the sandbox can open as
    that role after ``DISCARD ALL`` (SET ROLE is not viable). No PostgreSQL RLS.
    """
    from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (  # noqa: PLC0415
        query_role_name,
    )

    url = engine.url
    database = url.database or ""
    role = query_role_name(database)
    password = url.password or ""
    # Identifiers cannot be parameterized; the role name is derived from the
    # database name the deployment already owns.
    connection.execute(
        pg_sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            pg_sql.Identifier(role), pg_sql.Literal(password)
        )
    )


class SelfHostProfile:
    """Compose the complete continuous E/P1 path plus aggregate P2/P3 builds."""

    def __init__(
        self,
        *,
        settings: SelfHostSettings,
        engine: Engine,
        raw_store: MinIOObjectStore,
        artifact_store: MinIOObjectStore,
        corpusfs_store: MinIOObjectStore,
        snapshot_store: MinIOObjectStore,
        model_provider: OpenRouterModelProvider,
        error_telemetry: TelemetryPort | None = None,
    ) -> None:
        """Retain one dependency graph for an API, setup, or worker process."""
        self._settings = settings
        self._engine = engine
        self._raw_store = raw_store
        self._artifact_store = artifact_store
        self._corpusfs_store = corpusfs_store
        self._snapshot_store = snapshot_store
        self._model_provider = model_provider
        self._error_telemetry = error_telemetry

    @classmethod
    def from_settings(cls, *, error_telemetry: TelemetryPort | None = None) -> Self:
        """Load every external value through its typed settings boundary."""
        profile_settings = SelfHostSettings.model_validate({})
        minio_settings = MinIOSettings.model_validate({})
        return cls(
            settings=profile_settings,
            engine=sqlalchemy.create_engine(
                load_database_settings().sqlalchemy_url(), pool_pre_ping=True
            ),
            raw_store=MinIOObjectStore(
                bucket=profile_settings.raw_bucket_name, settings=minio_settings
            ),
            artifact_store=MinIOObjectStore(
                bucket=profile_settings.artifacts_bucket_name, settings=minio_settings
            ),
            corpusfs_store=MinIOObjectStore(
                bucket=profile_settings.corpusfs_bucket_name, settings=minio_settings
            ),
            snapshot_store=MinIOObjectStore(
                bucket=profile_settings.snapshot_bucket_name, settings=minio_settings
            ),
            model_provider=OpenRouterModelProvider(
                settings=OpenRouterSettings.model_validate({})
            ),
            error_telemetry=error_telemetry,
        )

    def close(self) -> None:
        """Dispose this process's explicitly owned database pool."""
        self._engine.dispose()

    def setup(self) -> None:
        """Apply migrations, provision stores, bootstrap, and seed operations."""
        migration = Config(str(self._settings.migration_config))
        migration.set_main_option(
            "sqlalchemy.url", load_database_settings().sqlalchemy_url()
        )
        command.upgrade(config=migration, revision="head")
        self._raw_store.ensure_bucket()
        self._artifact_store.ensure_bucket()
        self._corpusfs_store.ensure_bucket()
        self._snapshot_store.ensure_bucket()
        self._settings.forget_manifest_root.mkdir(parents=True, exist_ok=True)
        self._settings.projection_work_root.mkdir(parents=True, exist_ok=True)
        self._settings.graph_cache_root.mkdir(parents=True, exist_ok=True)
        DeploymentBootstrapper(engine=self._engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=self._settings.deployment_id,
                slug=self._settings.deployment_slug,
                name=self._settings.deployment_name,
                default_language=self._settings.default_language,
                raw_bucket=f"s3://{self._settings.raw_bucket_name}",
                artifacts_bucket=f"s3://{self._settings.artifacts_bucket_name}",
                corpusfs_bucket=f"s3://{self._settings.corpusfs_bucket_name}",
            )
        )
        seed_canonical_operations(
            registry=AssuredOperationRegistry(engine=self._engine),
            deployment_id=self._settings.deployment_id,
        )
        # Install the seventeen examples.* saved-query identities (idempotent).
        # Not an Alembic migration: deployment seed DML lives in setup/bootstrap.
        from rememberstack.spine.query_space.canonical import (  # noqa: PLC0415
            surface_manifest_hash,
        )
        from rememberstack.spine.query_space.manifest import (  # noqa: PLC0415
            build_hash_members,
        )
        from rememberstack.surfaces.query_sandbox.saved_queries import (  # noqa: PLC0415
            seed_shipped_examples,
        )

        with self._engine.connect() as sa_connection:
            raw = sa_connection.connection.dbapi_connection
            assert isinstance(raw, psycopg.Connection)
            seed_shipped_examples(
                connection=raw,
                deployment_id=self._settings.deployment_id,
                manifest_hash=surface_manifest_hash(build_hash_members()),
            )
            # Deploy-time only: provision the query-role password so setup-time
            # composition can open as that role later. Never run from api().
            _provision_query_role_password(connection=raw, engine=self._engine)
            # The bootstrap work above uses the raw psycopg connection, so its
            # transaction must be committed through that same connection.
            raw.commit()

    def api(self) -> FastAPI:
        """Build the existing HTTP surface over this self-host dependency graph."""
        from rememberstack.adapters.selfhost.lance import LanceChunkIndex
        from rememberstack.spine import DocumentCatalog
        from rememberstack.spine import ForgetCatalog
        from rememberstack.spine import PipelineReadinessCatalog
        from rememberstack.spine import ProjectionCatalog
        from rememberstack.spine.query_space.canonical import surface_manifest_hash
        from rememberstack.spine.query_space.manifest import build_hash_members
        from rememberstack.surfaces import build_api
        from rememberstack.surfaces import OperationExecutor
        from rememberstack.surfaces import OperationSurface
        from rememberstack.surfaces import QueryEngine
        from rememberstack.surfaces.query_sandbox.audit import AuditTrail
        from rememberstack.surfaces.query_sandbox.audit import KillSwitches
        from rememberstack.surfaces.query_sandbox.cypher_executor import (
            CypherSandboxExecutor,
        )
        from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
        from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
        from rememberstack.workers import E1Settings
        from rememberstack.workers import GraphSnapshotReader
        from rememberstack.workers.e0 import UploadIngestor

        # D80: query-side embedder_generation must match E1 write stamps.
        # E1 owns passage vectors; do not let REMEMBERSTACK_P1_EMBEDDING_MODEL
        # silently desync search from the embed stage.
        e1_settings = E1Settings.model_validate({})
        projection_catalog = ProjectionCatalog(engine=self._engine)
        graph_reader = GraphSnapshotReader(
            catalog=projection_catalog,
            snapshot_store=self._snapshot_store,
            deployment_id=self._settings.deployment_id,
            cache_dir=self._settings.graph_cache_root,
        )
        search_index = LanceChunkIndex(root=self._settings.lance_root)
        embedding_model = e1_settings.embedding_model
        # One admission + audit authority for SQL and Cypher so concurrency and
        # rolling spend are combined and §7 events actually emit.
        kill_switches = KillSwitches()
        audit_trail = AuditTrail()
        query_role_connect = _query_role_connect_factory(engine=self._engine)
        surface_cost = SqlSurfaceCostRecorder(
            engine=self._engine, deployment_id=self._settings.deployment_id
        )
        embed_query = partial(
            selfhost_embed_query,
            model_provider=self._model_provider,
            embedding_model=embedding_model,
            surface_cost=surface_cost,
            deployment_id=self._settings.deployment_id,
        )

        query_engine = QueryEngine(
            engine=self._engine,
            search_index=search_index,
            model_provider=self._model_provider,
            embedding_model=embedding_model,
            surface_cost=surface_cost,
        )
        sql_executor = QuerySandboxExecutor(
            deployment_id=self._settings.deployment_id,
            connect=query_role_connect,
            search=search_index,
            embed=embed_query,
            kill_switches=kill_switches,
            audit=audit_trail,
        )
        cypher_executor = CypherSandboxExecutor(
            deployment_id=self._settings.deployment_id,
            reader=graph_reader,
            connect=query_role_connect,
            kill_switches=kill_switches,
            audit=audit_trail,
        )
        manifest_hash = surface_manifest_hash(build_hash_members())
        open_query = OpenQueryFacade(
            deployment_id=self._settings.deployment_id,
            sql=sql_executor,
            cypher=cypher_executor,
            saved_queries=_SelfHostSavedQueryReads(
                engine=self._engine,
                deployment_id=self._settings.deployment_id,
                manifest_hash=manifest_hash,
            ),
        )
        app = build_api(
            engine=query_engine,
            deployment_id=self._settings.deployment_id,
            admission=ForgetCatalog(engine=self._engine),
            readiness=_FreshDeploymentReadiness(
                store=LocalFSForgetManifestStore(
                    root=self._settings.forget_manifest_root
                )
            ),
            surface=OperationSurface(
                registry=AssuredOperationRegistry(engine=self._engine),
                executor=OperationExecutor(query_engine=query_engine),
                deployment_id=self._settings.deployment_id,
            ),
            open_query=open_query,
            ingest=UploadIngestor(
                catalog=DocumentCatalog(engine=self._engine),
                raw_store=self._raw_store,
                admission=ForgetCatalog(engine=self._engine),
            ),
            pipeline_readiness=PipelineReadinessCatalog(
                engine=self._engine,
                expected_components=_expected_components(),
                projections=projection_catalog,
                model_bindings=_model_bindings(),
                build_revision=_build_revision(),
            ),
        )

        @app.get("/deployment", response_model=DeploymentBuildInfo)
        def deployment_build_info() -> DeploymentBuildInfo:
            """Report which code and model bindings are serving, before any work."""
            return DeploymentBuildInfo(
                build_revision=_build_revision(), model_bindings=_model_bindings()
            )

        @app.get("/healthz", include_in_schema=False)
        def healthz() -> dict[str, str]:
            """Prove the process can reach its authoritative PostgreSQL spine."""
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
            return {"status": "ok"}

        return app

    def worker_loop(self, *, stage: PipelineStage) -> SelfHostWorkerLoop:
        """Build one continuous route's ordinary LISTEN/NOTIFY worker loop."""
        from rememberstack.adapters.selfhost import FanoutTelemetry
        from rememberstack.adapters.selfhost import JsonLineTelemetry
        from rememberstack.adapters.selfhost import SelfHostTaskQueue
        from rememberstack.adapters.selfhost import SelfHostWorkerLoop
        from rememberstack.adapters.selfhost import TokenBucket
        from rememberstack.model import ProcessingLane
        from rememberstack.spine import WorkLedger
        from rememberstack.spine import WorkLedgerSettings
        from rememberstack.workers import HandlerRegistry
        from rememberstack.workers import Worker

        if stage not in _SUPPORTED_WORKER_STAGES:
            raise ValueError(f"the self-host profile has no handler for stage {stage}")
        registry = HandlerRegistry()
        registry.register(stage=stage, handler=self._handler(stage=stage))
        ledger = WorkLedger(engine=self._engine, settings=WorkLedgerSettings())
        local_telemetry = JsonLineTelemetry()
        telemetry = (
            local_telemetry
            if self._error_telemetry is None
            else FanoutTelemetry(sinks=(local_telemetry, self._error_telemetry))
        )
        return SelfHostWorkerLoop(
            worker=Worker(
                ledger=ledger,
                registry=registry,
                queue=SelfHostTaskQueue(ledger=ledger),
                telemetry=telemetry,
            ),
            deployment_id=self._settings.deployment_id,
            stage=stage,
            lane=ProcessingLane.STEADY,
            bucket=TokenBucket(
                rate_per_s=self._settings.worker_rate_per_s,
                capacity=self._settings.worker_burst,
            ),
            database_url=_psycopg_url(),
            fallback_poll_s=self._settings.worker_fallback_poll_s,
        )

    def run_worker(self, *, stage: PipelineStage) -> None:
        """Run one configured continuous route until stopped or failed."""
        loop = self.worker_loop(stage=stage)
        while True:
            loop.run_for(duration_s=self._settings.worker_session_s)

    def run_projection(self, *, plane: str) -> dict[str, object]:
        """Build P2, P3, or both once after continuous ingestion settles."""
        from rememberstack.spine import ForgetCatalog
        from rememberstack.spine import ProjectionCatalog
        from rememberstack.workers import CorpusFsBuilder
        from rememberstack.workers import GraphRebuildWorker

        ForgetCatalog(engine=self._engine).assert_available(
            deployment_id=self._settings.deployment_id
        )
        catalog = ProjectionCatalog(engine=self._engine)
        reports: dict[str, object] = {}
        if plane in {"p2", "all"}:
            reports["p2"] = GraphRebuildWorker(
                catalog=catalog, snapshot_store=self._snapshot_store
            ).rebuild(
                deployment_id=self._settings.deployment_id,
                workdir=self._settings.projection_work_root,
            )
        if plane in {"p3", "all"}:
            reports["p3"] = CorpusFsBuilder(
                catalog=catalog, snapshot_store=self._corpusfs_store
            ).build(deployment_id=self._settings.deployment_id)
        if not reports:
            raise ValueError(f"unknown projection plane {plane!r}")
        return reports

    def publish_mounts(self, *, root: Path) -> PublishedMounts:
        """Materialize the latest P3 snapshot under one local mount root."""
        from rememberstack.adapters.selfhost import LocalMountPublisher
        from rememberstack.spine import ForgetCatalog
        from rememberstack.spine import ProjectionCatalog

        return LocalMountPublisher(
            root=root,
            catalog=ProjectionCatalog(engine=self._engine),
            corpusfs_store=self._corpusfs_store,
            admission=ForgetCatalog(engine=self._engine),
        ).publish(deployment_id=self._settings.deployment_id)

    def _handler(self, *, stage: PipelineStage) -> StageHandler:
        """Compose exactly one implemented stage handler for one worker process."""
        from rememberstack.adapters.selfhost.lance import LanceChunkIndex
        from rememberstack.core import chunker_version
        from rememberstack.core import ChunkerParams
        from rememberstack.core import ConversionRouter
        from rememberstack.core import MarkdownPassthroughConverter
        from rememberstack.model import ResolverConfig
        from rememberstack.spine import CascadeResolver
        from rememberstack.spine import ChunkCatalog
        from rememberstack.spine import ClaimCatalog
        from rememberstack.spine import DocumentCatalog
        from rememberstack.spine import EntityRegistry
        from rememberstack.spine import FactCatalog
        from rememberstack.spine import LifecycleCatalog
        from rememberstack.spine import ObservationAdjudicator
        from rememberstack.spine import ObservationSettings
        from rememberstack.spine import RESOLVER_VERSION
        from rememberstack.spine import ReviewQueue
        from rememberstack.spine import SupersessionAdjudicator
        from rememberstack.spine import SupersessionSettings
        from rememberstack.workers import AdjudicateObservationsHandler
        from rememberstack.workers import AdjudicateSupersessionHandler
        from rememberstack.workers import ChunkHandler
        from rememberstack.workers import ConvertHandler
        from rememberstack.workers import E1Settings
        from rememberstack.workers import E2Settings
        from rememberstack.workers import E3Settings
        from rememberstack.workers import EmbedChunksHandler
        from rememberstack.workers import EmbedClaimsHandler
        from rememberstack.workers import ExtractClaimsHandler
        from rememberstack.workers import LabelFactsHandler
        from rememberstack.workers import NormalizeRelationsHandler
        from rememberstack.workers import P1Settings
        from rememberstack.workers import ReconcileHandler
        from rememberstack.workers import RoleSettings
        from rememberstack.workers import SkeletonCheckSettings
        from rememberstack.workers import StructureHandler
        from rememberstack.workers import StructurerSettings
        from rememberstack.workers import SummarySettings

        documents = DocumentCatalog(engine=self._engine)
        chunks = ChunkCatalog(engine=self._engine)
        claims = ClaimCatalog(engine=self._engine)
        facts = FactCatalog(engine=self._engine)
        index = LanceChunkIndex(root=self._settings.lance_root)
        params = ChunkerParams()
        chunk_generation = chunker_version(params=params)
        p1_settings = P1Settings.model_validate({})
        if stage is PipelineStage.CONVERT:
            return ConvertHandler(
                catalog=documents,
                raw_store=self._raw_store,
                artifact_store=self._artifact_store,
                router=ConversionRouter(
                    routes={"text/markdown": MarkdownPassthroughConverter()}
                ),
            )
        if stage is PipelineStage.STRUCTURE:
            return StructureHandler(
                catalog=documents,
                artifact_store=self._artifact_store,
                model_provider=self._model_provider,
                settings=StructurerSettings.model_validate({}),
                check_settings=SkeletonCheckSettings.model_validate({}),
                role_settings=RoleSettings.model_validate({}),
                summary_settings=SummarySettings.model_validate({}),
            )
        if stage is PipelineStage.CHUNK:
            return ChunkHandler(
                catalog=chunks, artifact_store=self._artifact_store, params=params
            )
        if stage is PipelineStage.EMBED_CHUNK:
            return EmbedChunksHandler(
                catalog=chunks,
                artifact_store=self._artifact_store,
                model_provider=self._model_provider,
                chunk_index=index,
                settings=E1Settings.model_validate({}),
                params=params,
            )
        if stage is PipelineStage.EXTRACT_CLAIMS:
            return ExtractClaimsHandler(
                catalog=claims,
                chunk_catalog=chunks,
                artifact_store=self._artifact_store,
                model_provider=self._model_provider,
                settings=E2Settings.model_validate({}),
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.NORMALIZE_RELATIONS:
            observation_settings = ObservationSettings.model_validate({})
            return NormalizeRelationsHandler(
                claim_catalog=claims,
                chunk_catalog=chunks,
                registry=EntityRegistry(engine=self._engine),
                resolver=CascadeResolver(
                    engine=self._engine,
                    entity_index=index,
                    model_provider=self._model_provider,
                    config=ResolverConfig(resolver_version=RESOLVER_VERSION),
                    embedding_model=observation_settings.embedding_model,
                    small_model=observation_settings.small_model,
                    frontier_model=observation_settings.frontier_model,
                ),
                facts=facts,
                observation_adjudicator=ObservationAdjudicator(
                    engine=self._engine,
                    model_provider=self._model_provider,
                    settings=observation_settings,
                ),
                model_provider=self._model_provider,
                settings=E3Settings.model_validate({}),
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.ADJUDICATE_OBSERVATIONS:
            observation_settings = ObservationSettings.model_validate({})
            return AdjudicateObservationsHandler(
                facts=facts,
                observation_adjudicator=ObservationAdjudicator(
                    engine=self._engine,
                    model_provider=self._model_provider,
                    settings=observation_settings,
                ),
                chunk_catalog=chunks,
                claim_catalog=claims,
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.ADJUDICATE_SUPERSESSION:
            return AdjudicateSupersessionHandler(
                adjudicator=SupersessionAdjudicator(
                    engine=self._engine,
                    model_provider=self._model_provider,
                    settings=SupersessionSettings.model_validate({}),
                ),
                facts=facts,
                chunk_catalog=chunks,
                claim_catalog=claims,
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.EMBED_CLAIM:
            return EmbedClaimsHandler(
                claim_catalog=claims,
                chunk_catalog=chunks,
                model_provider=self._model_provider,
                claim_index=index,
                settings=p1_settings,
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.RECONCILE:
            return ReconcileHandler(
                catalog=LifecycleCatalog(engine=self._engine),
                review_queue=ReviewQueue(engine=self._engine),
                chunker_version=chunk_generation,
            )
        if stage is PipelineStage.LABEL_RELATION:
            return LabelFactsHandler(
                facts=facts,
                model_provider=self._model_provider,
                fact_index=index,
                settings=p1_settings,
            )
        raise ValueError(f"the self-host profile has no handler for stage {stage}")


def create_api() -> FastAPI:
    """Uvicorn factory that initializes process-global API error tracking.

    The API process has no worker telemetry fanout, so the returned Sentry sink
    is intentionally unused after its process-global SDK initialization.
    """
    settings = SelfHostSettings.model_validate({})
    _initialize_error_tracking(command="api", deployment_slug=settings.deployment_slug)
    return SelfHostProfile.from_settings().api()


def main(argv: list[str] | None = None) -> int:
    """Run setup, API, a worker, projection, or local mount publication."""
    parser = argparse.ArgumentParser(description="rememberstack self-host profile")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="migrate and bootstrap the deployment")
    subparsers.add_parser("api", help="serve the deployment HTTP API")
    worker = subparsers.add_parser("worker", help="run one continuous worker route")
    worker.add_argument(
        "--stage",
        choices=tuple(stage.value for stage in _SUPPORTED_WORKER_STAGES),
        required=True,
    )
    projection = subparsers.add_parser(
        "project", help="build aggregate projections once"
    )
    projection.add_argument("--plane", choices=("p2", "p3", "all"), required=True)
    mounts = subparsers.add_parser(
        "mounts", help="publish the latest P3 snapshot under a local root"
    )
    mounts.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = SelfHostSettings.model_validate({})
    if args.command == "api":
        import uvicorn

        uvicorn.run(
            create_api(),
            host=settings.api_host,
            port=settings.api_port,
            access_log=True,
        )
        return 0
    error_telemetry = _initialize_error_tracking(
        command=args.command, deployment_slug=settings.deployment_slug
    )
    profile = SelfHostProfile.from_settings(error_telemetry=error_telemetry)
    try:
        if args.command == "setup":
            profile.setup()
            return 0
        if args.command == "project":
            print(profile.run_projection(plane=args.plane))
            return 0
        if args.command == "mounts":
            print(profile.publish_mounts(root=args.root).model_dump_json())
            return 0
        profile.run_worker(stage=PipelineStage(args.stage))
        return 0
    finally:
        profile.close()


def _initialize_error_tracking(
    *, command: str, deployment_slug: str
) -> TelemetryPort | None:
    """Initialize the optional Sentry sink only for long-lived profile entrypoints."""
    if command not in {"api", "setup", "worker"}:
        return None
    settings = SentrySettings.model_validate({})
    dsn = settings.configured_dsn()
    if dsn is None:
        return None
    from rememberstack.adapters.sentry import initialize_sentry

    environment = (settings.environment or "").strip() or deployment_slug
    return initialize_sentry(
        dsn=dsn, environment=environment, sample_rate=settings.sample_rate
    )


def _psycopg_url() -> str:
    """Remove SQLAlchemy's driver suffix for psycopg's native connection parser."""
    url = make_url(load_database_settings().sqlalchemy_url())
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _expected_components() -> dict[PipelineStage, str]:
    """The exact eleven continuous generations composed by this profile."""
    from rememberstack.spine import ADJUDICATOR_VERSION
    from rememberstack.workers import E0_CONVERT_VERSION
    from rememberstack.workers import E0_STRUCTURE_VERSION
    from rememberstack.workers import E1_CHUNK_VERSION
    from rememberstack.workers import E1_EMBED_VERSION
    from rememberstack.workers import E2_EXTRACTOR_VERSION
    from rememberstack.workers import E3_NORMALIZER_VERSION
    from rememberstack.workers import OBS_FLUSH_VERSION
    from rememberstack.workers import P1_EMBED_CLAIMS_VERSION
    from rememberstack.workers import RECONCILE_VERSION
    from rememberstack.workers.p1 import label_relation_component_version
    from rememberstack.workers.p1 import P1Settings

    return {
        PipelineStage.CONVERT: E0_CONVERT_VERSION,
        PipelineStage.STRUCTURE: E0_STRUCTURE_VERSION,
        PipelineStage.CHUNK: E1_CHUNK_VERSION,
        PipelineStage.EMBED_CHUNK: E1_EMBED_VERSION,
        PipelineStage.EXTRACT_CLAIMS: E2_EXTRACTOR_VERSION,
        PipelineStage.NORMALIZE_RELATIONS: E3_NORMALIZER_VERSION,
        PipelineStage.ADJUDICATE_OBSERVATIONS: OBS_FLUSH_VERSION,
        PipelineStage.ADJUDICATE_SUPERSESSION: ADJUDICATOR_VERSION,
        PipelineStage.EMBED_CLAIM: P1_EMBED_CLAIMS_VERSION,
        PipelineStage.RECONCILE: RECONCILE_VERSION,
        PipelineStage.LABEL_RELATION: label_relation_component_version(
            embedding_model=P1Settings().embedding_model
        ),
    }


def _build_revision() -> str:
    """Read the source revision stamped into this image at build time.

    Empty when the image was built without the build argument. Callers that
    need provenance treat empty as unknown rather than as agreement.
    """
    return BuildProvenanceSettings().build_revision


def _model_bindings() -> dict[str, str]:
    """Non-secret provider model identities used by the composed pipeline."""
    from rememberstack.spine import ObservationSettings
    from rememberstack.spine import SupersessionSettings
    from rememberstack.workers import E1Settings
    from rememberstack.workers import E2Settings
    from rememberstack.workers import E3Settings
    from rememberstack.workers import P1Settings
    from rememberstack.workers import RoleSettings
    from rememberstack.workers import SkeletonCheckSettings
    from rememberstack.workers import StructurerSettings
    from rememberstack.workers import SummarySettings

    structurer = StructurerSettings.model_validate({})
    skeleton_check = SkeletonCheckSettings.model_validate({})
    roles = RoleSettings.model_validate({})
    summaries = SummarySettings.model_validate({})
    e1 = E1Settings.model_validate({})
    e2 = E2Settings.model_validate({})
    e3 = E3Settings.model_validate({})
    observations = ObservationSettings.model_validate({})
    supersession = SupersessionSettings.model_validate({})
    p1 = P1Settings.model_validate({})
    openrouter = OpenRouterSettings.model_validate({})
    return {
        "structure_fallback": structurer.model,
        "skeleton_check": skeleton_check.model,
        "section_role": roles.model,
        "section_summary": summaries.model,
        "chunk_embedding": e1.embedding_model,
        "context_prefix": e1.prefix_model,
        "claim_extraction": e2.extract_model,
        "relation_normalization": e3.normalize_model,
        "entity_observation_embedding": observations.embedding_model,
        "observation_small": observations.small_model,
        "observation_frontier": observations.frontier_model,
        "supersession_small": supersession.small_model,
        "supersession_frontier": supersession.frontier_model,
        "p1_embedding": p1.embedding_model,
        "fact_label": p1.label_model,
        "openrouter_embedding_provider": openrouter.embedding_provider or "auto",
        "openrouter_embedding_provider_order": (
            ",".join(openrouter.embedding_provider_order)
            if openrouter.embedding_provider_order
            else "unset"
        ),
        "openrouter_max_completion_tokens": (
            str(openrouter.max_completion_tokens)
            if openrouter.max_completion_tokens is not None
            else "unset"
        ),
        "openrouter_reasoning_effort": openrouter.reasoning_effort or "auto",
        # Canonical (sorted-key) form so the effective per-model effort policy
        # is part of measurement provenance, not hidden behind the global pin.
        "openrouter_reasoning_effort_map": json.dumps(
            openrouter.reasoning_effort_map, sort_keys=True
        )
        if openrouter.reasoning_effort_map
        else "unset",
    }


if __name__ == "__main__":
    sys.exit(main())
