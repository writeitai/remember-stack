"""D155 publication transactions and the deployment's session advisory lock.

The existing work ledger, nullable marker and possibly written folder paths are
durable. Events carry a document id, never a desired revision/path.
Profile activation, lifecycle removal and scheduling are composed separately.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from rememberstack.core.corpus_export import corpus_policy_version
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine.corpus_export import _DOCUMENTS
from rememberstack.spine.work_ledger import enqueue_on


class CorpusLockLostError(RuntimeError):
    """The original lock session or this work's live claim no longer owns IO."""


def corpus_changed_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    policy: CorpusPolicy = DEFAULT_CORPUS_POLICY,
) -> EnqueueOutcome:
    """Invalidate and enqueue a fresh event in the authoritative transaction.

    The row update serializes with the final marker transaction. A later commit
    always gets a fresh target, including a return to earlier inputs (A→B→A).
    """
    connection.execute(
        text("""UPDATE documents SET corpus_published_revision = NULL
        WHERE deployment_id = :d AND doc_id = :doc"""),
        {"d": deployment_id, "doc": doc_id},
    )
    return enqueue_on(
        connection=connection,
        work=EnqueueWork(
            deployment_id=deployment_id,
            target_kind=ProcessingTarget.CORPUS_PUBLICATION,
            target_id=uuid4(),
            stage=PipelineStage.PUBLISH_CORPUS,
            component_version=corpus_policy_version(policy),
            content_hash=str(doc_id),
            lane=None,
            payload={"doc_id": str(doc_id)},
        ),
    )


def corpus_version_changed_on(
    *, connection: Connection, deployment_id: UUID, version_id: UUID
) -> None:
    """A canonical metadata change affects only a served version, not parked input."""
    doc_id = connection.execute(
        text("""SELECT doc_id FROM documents
        WHERE deployment_id = :d AND current_version_id = :v AND deleted_at IS NULL"""),
        {"d": deployment_id, "v": version_id},
    ).scalar_one_or_none()
    if doc_id is not None:
        corpus_changed_on(
            connection=connection, deployment_id=deployment_id, doc_id=doc_id
        )


def document_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> CorpusDocument | None:
    """Read current served inputs in the caller's transaction."""
    row = (
        connection.execute(
            text(_DOCUMENTS + " AND d.doc_id = :doc_id"),
            {"deployment_id": deployment_id, "doc_id": doc_id},
        )
        .mappings()
        .one_or_none()
    )
    return CorpusDocument(**dict(row)) if row is not None else None


@dataclass
class CorpusPublicationLock:
    """One checked-out connection; it must never reconnect and reacquire silently."""

    connection: Connection
    deployment_id: UUID
    backend_pid: int
    work: ClaimedWork | None = None

    def check(self) -> None:
        """Confirm actual lock ownership and live claim before a provider mutation."""
        if self.connection.closed or self.connection.invalidated:
            raise CorpusLockLostError("corpus publication lock session was lost")
        try:
            held = self.connection.execute(
                text("""SELECT pg_backend_pid() = :pid AND EXISTS (
                SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                AND pid = pg_backend_pid() AND granted AND mode = 'ExclusiveLock'
                AND classid = ((hashtextextended(:key, 0) >> 32) & 4294967295)::oid
                AND objid = (hashtextextended(:key, 0) & 4294967295)::oid
                AND objsubid = 1)"""),
                {
                    "key": f"corpus-publication:{self.deployment_id}",
                    "pid": self.backend_pid,
                },
            ).scalar_one()
            if held and self.work is not None:
                held = self.connection.execute(
                    text("""SELECT EXISTS (
                    SELECT 1 FROM processing_state WHERE processing_id = :p
                    AND deployment_id = :d AND status = 'running' AND attempts = :a
                    AND lease_until > clock_timestamp())"""),
                    {
                        "p": self.work.processing_id,
                        "d": self.deployment_id,
                        "a": self.work.attempt,
                    },
                ).scalar_one()
        except DBAPIError as error:
            raise CorpusLockLostError(
                "corpus publication lock session failed"
            ) from error
        if not held:
            raise CorpusLockLostError("corpus publication lost its lock or work claim")


class CorpusPublicationCatalog:
    """Own lock lifetime, folder/marker commits and current-policy seeding."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def locked(
        self, *, deployment_id: UUID, work: ClaimedWork | None = None
    ) -> Iterator[CorpusPublicationLock]:
        """Serialize publishers on a dedicated session, including all streamed IO."""
        with self._engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            params = {"key": f"corpus-publication:{deployment_id}"}
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), params
            )
            lock = CorpusPublicationLock(
                connection=connection,
                deployment_id=deployment_id,
                backend_pid=connection.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one(),
                work=work,
            )
            try:
                lock.check()
                yield lock
            finally:
                if not connection.invalidated and not connection.closed:
                    try:
                        connection.execute(
                            text(
                                "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                            ),
                            params,
                        )
                    except DBAPIError:
                        # A dead backend already released its session locks.
                        connection.invalidate()

    def clear(
        self, *, lock: CorpusPublicationLock, doc_id: UUID
    ) -> CorpusDocument | None:
        """Clear before store mutation, then read authority after acquiring the lock."""
        lock.check()
        with self._engine.begin() as connection:
            document = document_on(
                connection=connection, deployment_id=lock.deployment_id, doc_id=doc_id
            )
            connection.execute(
                text("""UPDATE documents SET corpus_published_revision = NULL
                WHERE deployment_id = :d AND doc_id = :doc"""),
                {"d": lock.deployment_id, "doc": doc_id},
            )
            return document

    def paths(self, *, lock: CorpusPublicationLock, doc_id: UUID) -> set[str]:
        """Read cleanup state even for a deleted or no-longer-served document."""
        lock.check()
        paths = lock.connection.execute(
            text("""SELECT corpus_paths FROM documents
            WHERE deployment_id = :d AND doc_id = :doc"""),
            {"d": lock.deployment_id, "doc": doc_id},
        ).scalar_one_or_none()
        return set(paths or ())

    def set_paths(
        self, *, lock: CorpusPublicationLock, doc_id: UUID, paths: set[str]
    ) -> None:
        """Commit on the lock session before IO; never reconnect after lock loss.

        Keep old paths until their navigation is repaired too, so a crash after
        the last old object deletion still leaves its ancestors discoverable.
        """
        lock.check()
        row = lock.connection.execute(
            text("""UPDATE documents SET corpus_paths = :paths
            WHERE deployment_id = :d AND doc_id = :doc RETURNING doc_id"""),
            {"d": lock.deployment_id, "doc": doc_id, "paths": sorted(paths) or None},
        ).scalar_one_or_none()
        if paths and row is None:
            raise RuntimeError("document disappeared before corpus path recording")

    def clear_unselected_paths(
        self, *, lock: CorpusPublicationLock, doc_ids: list[UUID]
    ) -> None:
        """Full reconcile removed all folders outside the served DB selection."""
        lock.check()
        lock.connection.execute(
            text("""UPDATE documents SET corpus_paths = NULL
            WHERE deployment_id = :d AND NOT (doc_id = ANY(CAST(:ids AS uuid[])))
            AND corpus_paths IS NOT NULL"""),
            {"d": lock.deployment_id, "ids": doc_ids},
        )

    def finish(
        self,
        *,
        lock: CorpusPublicationLock,
        doc_id: UUID,
        revision: str | None,
        policy: CorpusPolicy,
    ) -> bool:
        """Set success only against matching inputs; retain a durable newer wake-up."""
        with self._engine.begin() as connection:
            connection.execute(
                text("""SELECT doc_id FROM documents
                WHERE deployment_id = :d AND doc_id = :doc FOR UPDATE"""),
                {"d": lock.deployment_id, "doc": doc_id},
            )
            lock.check()
            current = document_on(
                connection=connection, deployment_id=lock.deployment_id, doc_id=doc_id
            )
            desired = (
                desired_export_revision(current, policy=policy) if current else None
            )
            if desired != revision:
                corpus_changed_on(
                    connection=connection,
                    deployment_id=lock.deployment_id,
                    doc_id=doc_id,
                    policy=policy,
                )
                return False
            if current:
                connection.execute(
                    text("""UPDATE documents SET corpus_published_revision = :r
                    WHERE deployment_id = :d AND doc_id = :doc"""),
                    {"d": lock.deployment_id, "doc": doc_id, "r": revision},
                )
            return True

    def seed_current_policy(
        self,
        *,
        deployment_id: UUID,
        policy: CorpusPolicy = DEFAULT_CORPUS_POLICY,
        batch_size: int = 500,
    ) -> int:
        """Seed each lineage lacking work for this policy, in bounded transactions.

        Existing succeeded/dead-letter work stays intact; seeding never resets
        attempts or silently retries a DLQ. Each policy cut uses ordinary events.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        total = 0
        while True:
            with self._engine.begin() as connection:
                ids = list(
                    connection.execute(
                        text("""SELECT d.doc_id FROM documents d
                    WHERE d.deployment_id = :d AND NOT EXISTS (
                        SELECT 1 FROM processing_state p WHERE p.deployment_id = d.deployment_id
                        AND p.stage = 'publish_corpus' AND p.component_version = :policy
                        AND p.payload->>'doc_id' = d.doc_id::text)
                    ORDER BY d.doc_id LIMIT :batch FOR UPDATE OF d SKIP LOCKED"""),
                        {
                            "d": deployment_id,
                            "policy": corpus_policy_version(policy),
                            "batch": batch_size,
                        },
                    ).scalars()
                )
                for doc_id in ids:
                    corpus_changed_on(
                        connection=connection,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                        policy=policy,
                    )
                total += len(ids)
            if len(ids) < batch_size:
                return total
