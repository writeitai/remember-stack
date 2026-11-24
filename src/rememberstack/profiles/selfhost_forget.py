"""One explicit self-host composition for D159 forget: hide, sweep, reapply."""

from typing import Self
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import Forget
from rememberstack.model import PipelineStage
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.purge import CorpusCachePurgePort
from rememberstack.ports.purge import KGitPurgePort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.spine import EntityProfileRefresher
from rememberstack.spine import ForgetCatalog
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetService
from rememberstack.workers import HandlerRegistry
from rememberstack.workers.corpus_publication import CorpusPublisher
from rememberstack.workers.forget import DEFAULT_QUIET_PERIOD_S
from rememberstack.workers.forget import ForgetKnowledgeRebuilder


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
        knowledge_rebuilder: ForgetKnowledgeRebuilder | None = None,
        caches: CorpusCachePurgePort | None = None,
        quiet_period_s: float = DEFAULT_QUIET_PERIOD_S,
    ) -> Self:
        """D147: compose the hide and the sweep over the deployment's actual stores.

        The stock API profile has no K store. If K pages exist, the host must
        supply both K capabilities; their absence never silently skips them.
        """
        catalog = ForgetCatalog(engine=engine)
        service = ForgetService(engine=engine, catalog=catalog)
        absent_k = _NoKnowledgeStore(engine=engine)
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
            knowledge_rebuilder=knowledge_rebuilder or absent_k,
            k_git=k_git or absent_k,
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
    """The source/claims/facts profile has no Plane-K serving store to purge."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self._refuse(deployment_id=deployment_id, artifact_ids=artifact_ids)

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self._refuse(deployment_id=deployment_id, artifact_ids=artifact_ids)

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self._refuse(deployment_id=deployment_id, artifact_ids=artifact_ids)

    def _refuse(self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]) -> None:
        if not artifact_ids:
            return
        with self._engine.connect() as connection:
            present = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM knowledge_artifacts"
                    " WHERE deployment_id = :d AND artifact_id = ANY(CAST(:ids AS uuid[])))"
                ),
                {"d": deployment_id, "ids": list(artifact_ids)},
            ).scalar_one()
        if present:
            raise RuntimeError(
                "K pages require a composed K purge and rebuild capability"
            )
