"""Sync-cycle catalog (lifecycle §2/§5, F8): the poll pass as explicit state.

One `connector_sync_cycles` row per pass, every ingested version stamped with
its cycle — the retract-timing barrier reconciliation reads. `completed_at`
is the poll pass ending; `finalized_at` belongs to the reconciliation
finalization job.
"""

from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine


class SyncCatalog:
    """Cycle rows, known revisions, and source archives."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def open_cycle(self, *, deployment_id: UUID, source_kind: str) -> UUID:
        """Record the start of one poll pass."""
        cycle_id = uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                _OPEN_CYCLE,
                {
                    "cycle_id": cycle_id,
                    "deployment_id": deployment_id,
                    "source_kind": source_kind,
                },
            )
        return cycle_id

    def complete_cycle(self, *, cycle_id: UUID, observed: int, failed: int) -> None:
        """The poll pass ended (finalization is reconciliation's job).

        ``failed`` counts items lost to per-item errors: a lossy cycle says
        so on its own row, because reconciliation must never treat its
        observation set as complete.
        """
        with self._engine.begin() as connection:
            connection.execute(
                _COMPLETE_CYCLE,
                {"cycle_id": cycle_id, "observed": observed, "failed": failed},
            )

    def known_revisions(
        self, *, deployment_id: UUID, source_kind: str
    ) -> dict[str, str]:
        """Each live lineage's last ingested revision (the no-fetch key).

        The cursor is the LATEST version's source revision, not the current
        pointer's: a just-ingested version whose chain is still running
        already covers its revision — reading the currency pointer instead
        would refetch the in-flight content on every poll.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                _KNOWN_REVISIONS,
                {"deployment_id": deployment_id, "source_kind": source_kind},
            ).all()
        return {source_ref: revision or "" for source_ref, revision in rows}

    def observe_deletion(
        self, *, deployment_id: UUID, source_kind: str, source_ref: str
    ) -> UUID | None:
        """Archive a source-deleted lineage (``archived_by = source``, D159).

        The file existed and said what it said, so a source deletion is
        history, not a mistake. The lineage stops being listed at once; its
        testimony cascade is the cycle finalizer's job (the retract-timing
        barrier), under the archive's reconciliation id. A lineage already
        archived (by the caller or an earlier observation) is left as it is.
        The source undoes it by showing the file again.
        """
        from rememberstack.spine.lifecycle import LifecycleCatalog
        from rememberstack.spine.processing_layers import take_layer_fence_on

        with self._engine.begin() as connection:
            # D143 §3.3 lock order: the layer fence before the lineage lock.
            take_layer_fence_on(connection=connection, deployment_id=deployment_id)
            doc_id = connection.execute(
                _LOCK_LIVE_LINEAGE,
                {
                    "deployment_id": deployment_id,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                },
            ).scalar_one_or_none()
            if doc_id is not None:
                LifecycleCatalog.on_connection(connection=connection).set_archived(
                    doc_id=doc_id, archived_by="source"
                )
            return doc_id


_OPEN_CYCLE = text(
    """
    INSERT INTO connector_sync_cycles (cycle_id, deployment_id, source_kind)
    VALUES (:cycle_id, :deployment_id, :source_kind)
    """
)

_COMPLETE_CYCLE = text(
    """
    UPDATE connector_sync_cycles
    SET completed_at = now(), observed_lineages = :observed,
        failed_items = :failed
    WHERE cycle_id = :cycle_id
    """
)

_KNOWN_REVISIONS = text(
    """
    SELECT DISTINCT ON (d.doc_id) d.source_ref, v.source_version_ref
    FROM documents d
    LEFT JOIN document_versions v ON v.doc_id = d.doc_id
    WHERE d.deployment_id = :deployment_id
      AND d.source_kind = :source_kind
      AND d.deleted_at IS NULL
      -- a source archive is re-fetched, so the file reappearing undoes it;
      -- a caller archive keeps its revision and is undone only by a change
      AND d.archived_by IS DISTINCT FROM 'source'
      AND d.source_ref IS NOT NULL
    ORDER BY d.doc_id, v.version_no DESC NULLS LAST
    """
)

_LOCK_LIVE_LINEAGE = text(
    """
    SELECT doc_id FROM documents
    WHERE deployment_id = :deployment_id
      AND source_kind = :source_kind
      AND source_ref = :source_ref
      AND deleted_at IS NULL
      AND archived_at IS NULL
    FOR UPDATE
    """
)
