"""One explicit self-host composition for D74 hard-forget and readiness."""

from datetime import datetime
from pathlib import Path
from typing import Self
from uuid import UUID

from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSForgetManifestStore
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import ForgetManifest
from rememberstack.model import PipelineStage
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import LifecycleCatalog
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.spine.surface_cost import SurfaceCallSite
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.spine.surface_cost import SurfaceCostMeter
from rememberstack.workers import CorpusForgetRebuilder
from rememberstack.workers import CorpusFsBuilder
from rememberstack.workers import DeletionService
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import HardForgetHandler
from rememberstack.workers import HardForgetReadiness
from rememberstack.workers import HardForgetService
from rememberstack.workers import KnowledgeCommitDriver
from rememberstack.workers import KnowledgeCycleForgetRebuilder


class SelfHostHardForget:
    """The self-host request, worker registration, admission, and startup gate."""

    def __init__(
        self,
        *,
        engine: Engine,
        catalog: ForgetCatalog,
        service: HardForgetService,
        handler: HardForgetHandler,
        readiness: HardForgetReadiness,
    ) -> None:
        """Retain one shared coordinator graph for every self-host surface."""
        self._engine = engine
        self._catalog = catalog
        self._service = service
        self._handler = handler
        self._readiness = readiness

    @classmethod
    def compose(
        cls,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        embedding_model: str,
        manifest_root: Path,
        object_roots: tuple[Path, ...],
        snapshot_root: Path,
        mount_root: Path,
        knowledge_repository: Path,
        knowledge_author_name: str,
        knowledge_author_email: str,
        knowledge_driver: KnowledgeCommitDriver,
    ) -> Self:
        """Compose only existing production workers and selected self-host stores."""
        if not object_roots:
            raise ValueError("hard-forget requires at least one ordinary object root")
        catalog = ForgetCatalog(engine=engine)
        manifest_store = LocalFSForgetManifestStore(root=manifest_root)
        object_purgers = tuple(LocalFSObjectStore(root=root) for root in object_roots)
        snapshot_store = LocalFSObjectStore(root=snapshot_root)
        projection_catalog = ProjectionCatalog(engine=engine)
        k_git = LocalGitRepository(
            repository=knowledge_repository,
            path_catalog=catalog,
            author_name=knowledge_author_name,
            author_email=knowledge_author_email,
        )
        service = HardForgetService(
            catalog=catalog, manifest_store=manifest_store, k_git=k_git
        )
        profile_refresher = EntityProfileRefresher(
            engine=engine,
            model_provider=model_provider,
            embedding_model=embedding_model,
        )
        handler = HardForgetHandler(
            catalog=catalog,
            deletion=DeletionService(
                catalog=LifecycleCatalog(engine=engine),
                profile_refresher=profile_refresher,
            ),
            profile_refresher=profile_refresher,
            object_purgers=object_purgers,
            projection_rebuilder=CorpusForgetRebuilder(
                corpus=CorpusFsBuilder(
                    catalog=projection_catalog, snapshot_store=snapshot_store
                )
            ),
            projection_purger=SelfHostProjectionPurger(
                object_purger=snapshot_store,
                catalog=projection_catalog,
                mount_root=mount_root,
            ),
            knowledge_rebuilder=KnowledgeCycleForgetRebuilder(driver=knowledge_driver),
            k_git=k_git,
        )
        readiness = HardForgetReadiness(
            catalog=catalog,
            manifest_store=manifest_store,
            request_service=service,
            handler=handler,
        )
        return cls(
            engine=engine,
            catalog=catalog,
            service=service,
            handler=handler,
            readiness=readiness,
        )

    def register(self, *, registry: HandlerRegistry) -> None:
        """Install the one unlaned hard-forget worker handler."""
        registry.register(stage=PipelineStage.HARD_FORGET, handler=self._handler)

    def request(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        requested_at: datetime,
    ) -> ForgetManifest:
        """Submit one lineage request through the crash-safe acceptance cut."""
        return self._service.request(
            deployment_id=deployment_id,
            doc_id=doc_id,
            forget_id=forget_id,
            requested_at=requested_at,
        )

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Re-honor every portable manifest before serving begins."""
        meter = SurfaceCostMeter(
            recorder=SqlSurfaceCostRecorder(
                engine=self._engine, deployment_id=deployment_id
            ),
            deployment_id=deployment_id,
            call_site=SurfaceCallSite.PROFILE_FORGET_RECOVERY,
        )
        with open_surface_scope(surface=SurfaceCostKind.OPERATION):
            return self._readiness.ensure_ready(
                deployment_id=deployment_id, meter=meter
            )

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Implement the shared public/mount admission perimeter."""
        self._catalog.assert_available(deployment_id=deployment_id)

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Implement E0's before-bytes barrier and irreversible ingest guard."""
        self._catalog.guard_ingest(
            deployment_id=deployment_id,
            source_kind=source_kind,
            source_ref=source_ref,
            content_hash=content_hash,
        )
