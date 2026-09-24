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
    """Cycle rows, known revisions, and deletion tombstones."""

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
        self, *, deployment_id: UUID, source_kind: str, source_ref: str, cycle_id: UUID
    ) -> UUID | None:
        """Tombstone a source-deleted lineage (loud, recorded, idempotent).

        The tombstone is stamped with the observing cycle so reconciliation
        can place the deletion inside its cycle barrier. The downstream
        cascade (claims currency, fact closure per mode, artifact removal)
        is the delete worker's job. Every version is tombstoned with the
        lineage, as for an operator delete, so a file recreated in a later
        cycle is processed as a new version instead of matching a deleted
        one (D135).
        """
        with self._engine.begin() as connection:
            doc_id = connection.execute(
                _TOMBSTONE_LINEAGE,
                {
                    "deployment_id": deployment_id,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "cycle_id": cycle_id,
                },
            ).scalar_one_or_none()
            if doc_id is not None:
                connection.execute(_TOMBSTONE_LINEAGE_VERSIONS, {"doc_id": doc_id})
                # D102/D135: deleted evidence never anchors identity; a
                # recreated file earns fresh anchors from its new version.
                connection.execute(
                    _CLEAR_DOCUMENT_BINDINGS,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
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
      AND d.source_ref IS NOT NULL
    ORDER BY d.doc_id, v.version_no DESC NULLS LAST
    """
)

_TOMBSTONE_LINEAGE = text(
    """
    UPDATE documents
    SET deleted_at = now(), deleted_sync_cycle_id = :cycle_id
    WHERE deployment_id = :deployment_id
      AND source_kind = :source_kind
      AND source_ref = :source_ref
      AND deleted_at IS NULL
    RETURNING doc_id
    """
)

_CLEAR_DOCUMENT_BINDINGS = text(
    """
    DELETE FROM document_entity_bindings
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_TOMBSTONE_LINEAGE_VERSIONS = text(
    """
    UPDATE document_versions SET deleted_at = now()
    WHERE doc_id = :doc_id AND deleted_at IS NULL
    """
)
