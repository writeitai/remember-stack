"""One explicit self-host composition for D159 forget: hide, sweep, reapply."""

from typing import Self
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import Forget
from rememberstack.model import KnowledgeErasure
from rememberstack.model import PipelineStage
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.purge import CorpusCachePurgePort
from rememberstack.ports.purge import KGitPurgePort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetService
from rememberstack.workers import HandlerRegistry
from rememberstack.workers.corpus_publication import CorpusPublisher
from rememberstack.workers.forget import DEFAULT_QUIET_PERIOD_S
from rememberstack.workers.forget import ForgetKnowledge
from rememberstack.workers.forget import ForgetKnowledgeEraser


class SelfHostForget:
    """The internal request entry point and the sweep worker over selected stores."""

    def __init__(self, *, service: ForgetService, handler: ForgetHandler) -> None:
        """Retain one shared forget graph for every self-host surface."""
        self._service = service
        self._handler = handler

    @classmethod
    def from_stores(
        cls,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        embedding_model: str,
        object_purgers: tuple[ObjectPurgePort, ...],
        corpus_store: ObjectPurgePort,
        publisher: CorpusPublisher,
        k_git: KGitPurgePort | None = None,
        caches: CorpusCachePurgePort | None = None,
        quiet_period_s: float = DEFAULT_QUIET_PERIOD_S,
    ) -> Self:
        """D147: compose the hide and the sweep over the deployment's actual stores.

        The stock API profile has no K store. If K pages exist, the host must
        supply the K repository; its absence never silently skips them.
        """
        catalog = ForgetCatalog(engine=engine)
        service = ForgetService(engine=engine, catalog=catalog)
        knowledge: ForgetKnowledge = (
            _NoKnowledgeStore(engine=engine)
            if k_git is None
            else ForgetKnowledgeEraser(
                catalog=catalog,
                control_plane=KnowledgeControlPlane(engine=engine),
                repository=k_git,
            )
        )
        handler = ForgetHandler(
            service=service,
            catalog=catalog,
            profile_refresher=EntityProfileRefresher(
                engine=engine,
                model_provider=model_provider,
                embedding_model=embedding_model,
            ),
            object_purgers=object_purgers,
            projection_purger=SelfHostProjectionPurger(
                publisher=publisher, object_purger=corpus_store, caches=caches
            ),
            knowledge=knowledge,
            quiet_period_s=quiet_period_s,
        )
        return cls(
            service=ForgetService(engine=engine, catalog=catalog), handler=handler
        )

    @property
    def handler(self) -> ForgetHandler:
        """The sweep worker for the ``forget`` stage."""
        return self._handler

    def register(self, *, registry: HandlerRegistry) -> None:
        """Install the one unlaned forget worker handler."""
        registry.register(stage=PipelineStage.FORGET, handler=self._handler)

    def request(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID | None = None
    ) -> tuple[Forget, bool]:
        """Hide one lineage and start its sweeps; a repeat returns the record."""
        return self._service.request(
            deployment_id=deployment_id, doc_id=doc_id, forget_id=forget_id
        )


class _NoKnowledgeStore:
    """The source/claims/facts profile has no Plane-K repository to erase."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...], first: bool
    ) -> KnowledgeErasure:
        del first
        if drop_artifact_ids:
            with self._engine.connect() as connection:
                present = connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM knowledge_artifacts"
                        " WHERE deployment_id = :d"
                        " AND artifact_id = ANY(CAST(:ids AS uuid[])))"
                    ),
                    {"d": forget.deployment_id, "ids": list(drop_artifact_ids)},
                ).scalar_one()
            if present:
                raise RuntimeError("K pages require a composed K repository")
        return KnowledgeErasure(removed=False)

    def flag(self, *, forget: Forget, paths: tuple[str, ...]) -> None:
        del forget, paths
