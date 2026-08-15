"""Straight-line D74 request admission and portable-manifest acceptance."""

from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from rememberstack.model import ClaimedWork
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestStatus
from rememberstack.model import ForgetRedactionRequiredError
from rememberstack.model import NonRetryableHandlerError
from rememberstack.ports import ForgetManifestPort
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.spine import ForgetCatalog
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.knowledge_driver import KnowledgeCommitDriver
from rememberstack.workers.p2 import GraphRebuildWorker
from rememberstack.workers.p3 import CorpusFsBuilder
from rememberstack.workers.reconcile import DeletionService


class ForgetProjectionRebuilder(Protocol):
    """Publish clean P2/P3 snapshots from the scrubbed authoritative spine."""

    def rebuild_without_lineage(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        """Build and publish both clean projections idempotently."""
        ...


class ForgetKnowledgeRebuilder(Protocol):
    """Recompile affected machine-owned K pages after source removal."""

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Publish sanitized current compiled files before history erasure."""
        ...


class ProjectionPairForgetRebuilder:
    """Publish clean P2 and P3 through the existing whole-rebuild workers."""

    def __init__(
        self, *, graph: GraphRebuildWorker, corpus: CorpusFsBuilder, workdir: Path
    ) -> None:
        """Bind the two production builders and one explicit scratch root."""
        self._graph = graph
        self._corpus = corpus
        self._workdir = workdir

    def rebuild_without_lineage(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        """Publish both clean pointers; retries may safely publish a newer pair."""
        self._graph.rebuild(
            deployment_id=deployment_id, workdir=self._workdir / str(forget_id)
        )
        self._corpus.build(deployment_id=deployment_id)


class KnowledgeCycleForgetRebuilder:
    """Compile the affected stale pages through the existing single K driver."""

    def __init__(self, *, driver: KnowledgeCommitDriver) -> None:
        """Bind the production commit cycle after PostgreSQL marks pages stale."""
        self._driver = driver

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Run one K commit only when the manifest names affected artifacts."""
        if artifact_ids:
            self._driver.run_cycle(
                deployment_id=deployment_id, exclusions_by_artifact={}
            )


class HardForgetService:
    """Prepare, freeze, durably append, and enqueue one lineage forget."""

    def __init__(
        self,
        *,
        catalog: ForgetCatalog,
        manifest_store: ForgetManifestPort,
        k_git: KGitPurgePort,
    ) -> None:
        """Bind the local materialization and the two portable boundaries."""
        self._catalog = catalog
        self._manifest_store = manifest_store
        self._k_git = k_git

    def request(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        requested_at: datetime,
    ) -> ForgetManifest:
        """Accept one request only after its exact manifest is durably appended."""
        existing = self._catalog.record_for_doc(
            deployment_id=deployment_id, doc_id=doc_id
        )
        if existing is None:
            self._require_redacted(deployment_id=deployment_id, doc_id=doc_id)
        progress = self._catalog.prepare(
            deployment_id=deployment_id, doc_id=doc_id, forget_id=forget_id
        )
        if progress.status in (
            ForgetManifestStatus.ACCEPTED,
            ForgetManifestStatus.COMPLETE,
        ):
            if progress.manifest is None:
                raise RuntimeError(
                    f"accepted forget_id {forget_id} has no local manifest"
                )
            self._manifest_store.append(manifest=progress.manifest)
            return progress.manifest
        manifest = progress.manifest
        if manifest is None:
            if not self._catalog.ordinary_work_is_drained(deployment_id=deployment_id):
                raise ForgetInProgressError(
                    f"forget_id {forget_id} is waiting for ordinary work to drain"
                )
            try:
                self._require_redacted(deployment_id=deployment_id, doc_id=doc_id)
            except ForgetRedactionRequiredError:
                self._catalog.cancel_unstored_preparation(
                    deployment_id=deployment_id, forget_id=forget_id
                )
                raise
            manifest = self._catalog.inventory_and_store_manifest(
                deployment_id=deployment_id,
                doc_id=doc_id,
                forget_id=forget_id,
                requested_at=requested_at,
            )
        self._manifest_store.append(manifest=manifest)
        self._catalog.accept_and_enqueue(manifest=manifest)
        return manifest

    def _require_redacted(self, *, deployment_id: UUID, doc_id: UUID) -> None:
        """Refuse owner-controlled K citations without rewriting their prose."""
        paths = tuple(
            sorted(
                set(
                    self._k_git.blocking_redaction_paths(
                        deployment_id=deployment_id, doc_id=doc_id
                    )
                )
            )
        )
        if paths:
            raise ForgetRedactionRequiredError(paths=paths)


class HardForgetHandler:
    """Run one accepted manifest through the single resumable purge sequence."""

    def __init__(
        self,
        *,
        catalog: ForgetCatalog,
        deletion: DeletionService,
        object_purgers: tuple[ObjectPurgePort, ...],
        projection_rebuilder: ForgetProjectionRebuilder,
        projection_purger: ProjectionPurgePort,
        knowledge_rebuilder: ForgetKnowledgeRebuilder,
        k_git: KGitPurgePort,
    ) -> None:
        """Bind existing lifecycle/rebuild services and narrow purge capabilities."""
        if not object_purgers:
            raise ValueError("hard-forget requires at least one object-store purger")
        self._catalog = catalog
        self._deletion = deletion
        self._object_purgers = object_purgers
        self._projection_rebuilder = projection_rebuilder
        self._projection_purger = projection_purger
        self._knowledge_rebuilder = knowledge_rebuilder
        self._k_git = k_git

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Honor every idempotent stage in order and reopen only after verification."""
        del meter
        forget_id = _forget_id(work=work)
        manifest = self._catalog.manifest_for(
            deployment_id=work.deployment_id, forget_id=forget_id
        )
        self.honor(manifest=manifest)
        return HandlerOutcome()

    def honor(self, *, manifest: ForgetManifest) -> None:
        """Run the shared purge path used by both the worker and readiness replay."""
        self._deletion.delete_lineage(
            deployment_id=manifest.deployment_id, doc_id=manifest.doc_id
        )
        self._catalog.scrub_postgres(manifest=manifest)
        for purger in self._object_purgers:
            purger.purge_objects(keys=manifest.object_keys, prefixes=())
        self._projection_rebuilder.rebuild_without_lineage(
            deployment_id=manifest.deployment_id, forget_id=manifest.forget_id
        )
        self._projection_purger.purge_projections(
            deployment_id=manifest.deployment_id, prefixes=manifest.projection_prefixes
        )
        self._knowledge_rebuilder.recompile_without_lineage(
            deployment_id=manifest.deployment_id, artifact_ids=manifest.k_artifact_ids
        )
        self._k_git.purge_artifacts(
            deployment_id=manifest.deployment_id,
            forget_id=manifest.forget_id,
            artifact_ids=manifest.k_artifact_ids,
        )
        for purger in self._object_purgers:
            purger.verify_objects_purged(keys=manifest.object_keys, prefixes=())
        self._projection_purger.verify_projections_purged(
            deployment_id=manifest.deployment_id, prefixes=manifest.projection_prefixes
        )
        self._k_git.verify_artifacts_purged(
            deployment_id=manifest.deployment_id,
            forget_id=manifest.forget_id,
            artifact_ids=manifest.k_artifact_ids,
        )
        self._catalog.verify_postgres_scrubbed(manifest=manifest)
        self._catalog.mark_complete(manifest=manifest)


class HardForgetReadiness:
    """Re-honor portable intent before one deployment accepts serving traffic."""

    def __init__(
        self,
        *,
        catalog: ForgetCatalog,
        manifest_store: ForgetManifestPort,
        request_service: HardForgetService,
        handler: HardForgetHandler,
    ) -> None:
        """Bind one source of intent to the same request and purge implementations."""
        self._catalog = catalog
        self._manifest_store = manifest_store
        self._request_service = request_service
        self._handler = handler

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Recover preparation, rematerialize, and re-honor every portable manifest."""
        pending = self._catalog.preparing_record(deployment_id=deployment_id)
        if pending is not None:
            self._request_service.request(
                deployment_id=pending.deployment_id,
                doc_id=pending.doc_id,
                forget_id=pending.forget_id,
                requested_at=(
                    pending.manifest.requested_at
                    if pending.manifest is not None
                    else pending.prepared_at
                ),
            )
        manifests = self._manifest_store.manifests(deployment_id=deployment_id)
        for manifest in manifests:
            self._catalog.materialize_portable(manifest=manifest)
            self._handler.honor(manifest=manifest)
        return tuple(manifest.forget_id for manifest in manifests)


def _forget_id(*, work: ClaimedWork) -> UUID:
    """Read the required portable identity from one accepted worker payload."""
    value = (work.payload or {}).get("forget_id")
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"hard-forget work {work.processing_id} carries no forget_id"
        )
    try:
        return UUID(value)
    except ValueError as error:
        raise NonRetryableHandlerError(
            f"hard-forget work {work.processing_id} has invalid forget_id"
        ) from error
