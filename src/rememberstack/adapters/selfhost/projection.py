"""D74 acknowledgement of document-scoped corpus and controlled cache erasure."""

from uuid import UUID

from rememberstack.model import ObjectPurgeEvidence
from rememberstack.ports.purge import CorpusCachePurgePort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.workers.corpus_publication import CorpusPublisher


class SelfHostProjectionPurger:
    """Erase corpus bytes/history and regenerate indexes without recopying survivors.

    No local snapshot materialization exists. An operator-managed FUSE mount
    must be stopped/remounted by its operator. A host with controlled sessions
    supplies ``caches``; acknowledgements are mandatory and failures propagate.
    """

    def __init__(
        self,
        *,
        publisher: CorpusPublisher,
        object_purger: ObjectPurgePort,
        caches: CorpusCachePurgePort | None = None,
    ) -> None:
        self._publisher = publisher
        self._objects = object_purger
        self._caches = caches

    def quiesce_projections(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        if self._caches is not None:
            self._caches.quiesce(deployment_id=deployment_id, forget_id=forget_id)

    def purge_projections(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID
    ) -> ObjectPurgeEvidence | None:
        evidence = self._publisher.erase_document(
            deployment_id=deployment_id,
            doc_id=doc_id,
            forget_id=forget_id,
            purger=self._objects,
        )
        if self._caches is not None:
            self._caches.purge(
                deployment_id=deployment_id, doc_id=doc_id, forget_id=forget_id
            )
        return evidence

    def verify_projections_purged(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID
    ) -> None:
        self._publisher.verify_erased(
            deployment_id=deployment_id,
            doc_id=doc_id,
            forget_id=forget_id,
            purger=self._objects,
        )
        if self._caches is not None:
            self._caches.verify(
                deployment_id=deployment_id, doc_id=doc_id, forget_id=forget_id
            )
