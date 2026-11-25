"""D159 corpus erasure over the selected corpus store and mount sessions."""

import logging
from uuid import UUID

from rememberstack.ports.purge import CorpusCachePurgePort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.workers.corpus_publication import CorpusPublisher

_logger = logging.getLogger(__name__)


class SelfHostProjectionPurger:
    """Erase a document's corpus folders, rewrite navigation, invalidate mounts.

    Takes no publication lock. Controlled mount sessions (``caches``) are told
    to drop the document's paths best effort; an operator-managed S3 FUSE
    mount stops listing them when its bounded cache expires.
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

    def erase_document(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        removed, rewritten = self._publisher.erase_document(
            deployment_id=deployment_id, doc_id=doc_id, purger=self._objects
        )
        if self._caches is not None:
            try:
                self._caches.invalidate(
                    deployment_id=deployment_id, doc_id=doc_id, paths=rewritten
                )
            except Exception:  # error-contract: expected  # noqa: BLE001
                _logger.warning(
                    "forget.mount_invalidation_failed doc_id=%s", doc_id, exc_info=True
                )
        return removed
