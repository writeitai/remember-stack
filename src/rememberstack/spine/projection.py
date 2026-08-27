"""The P3 CorpusFS snapshot registry and consistent export reads."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy import TextClause
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine


class CorpusExport:
    """One consistent corpus read for the P3 builder."""

    def __init__(self, *, connection: Connection, deployment_id: UUID) -> None:
        """Bind to the export connection and its deployment."""
        self._connection = connection
        self._deployment_id = deployment_id

    def documents(self) -> tuple[dict[str, object], ...]:
        """Every live lineage with its placement hint, summary, and pointers."""
        return self._rows(_SELECT_CORPUS_DOCUMENTS)

    def entities(self) -> tuple[dict[str, object], ...]:
        """Active entities with their profile and mention reach (P3 tier 1)."""
        return self._rows(_SELECT_CORPUS_ENTITIES)

    def entity_document_links(self) -> tuple[dict[str, object], ...]:
        """Which documents evidence which entity."""
        return self._rows(_SELECT_ENTITY_DOCUMENTS)

    def _rows(self, statement: TextClause) -> tuple[dict[str, object], ...]:
        """Run one export query on the shared snapshot."""
        return tuple(
            dict(row)
            for row in self._connection.execute(
                statement, {"deployment_id": self._deployment_id}
            ).mappings()
        )


class ProjectionCatalog:
    """P3 snapshot registry rows and consistent CorpusFS export reads."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def open_snapshot(
        self, *, deployment_id: UUID, plane: str, version: str, store_prefix: str
    ) -> UUID:
        """Register one building snapshot."""
        _require_p3_plane(plane)
        snapshot_id = uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_SNAPSHOT,
                {
                    "snapshot_id": snapshot_id,
                    "deployment_id": deployment_id,
                    "plane": plane,
                    "version": version,
                    "gcs_uri": store_prefix,
                },
            )
        return snapshot_id

    def mark_failed(self, *, snapshot_id: UUID, validation: dict[str, object]) -> None:
        """Record an aborted snapshot with its validation report (loudly)."""
        with self._engine.begin() as connection:
            connection.execute(
                _MARK_FAILED, {"snapshot_id": snapshot_id, "validation": validation}
            )

    def publish(
        self,
        *,
        deployment_id: UUID,
        snapshot_id: UUID,
        plane: str,
        row_counts: dict[str, int],
        validation: dict[str, object],
        built_from_watermark: object,
        built_at: object = None,
    ) -> bool:
        """Publish and swap the latest pointer — serialized and order-guarded.

        A per-(deployment, plane) advisory lock serializes concurrent
        publishers, and a snapshot whose build started BEFORE the currently
        published one never takes the pointer — a slow old rebuild finishing
        late must not regress readers. Such a snapshot is recorded as
        superseded (its bytes remain a point-in-time artifact); returns
        whether the pointer moved to this snapshot.
        """
        _require_p3_plane(plane)
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_PUBLISH, {"key": f"p3-publish:{deployment_id}:{plane}"}
            )
            newer = connection.execute(
                _SELECT_NEWER_LATEST,
                {
                    "deployment_id": deployment_id,
                    "plane": plane,
                    "snapshot_id": snapshot_id,
                    "built_at": built_at,
                },
            ).scalar_one_or_none()
            if newer is not None:
                connection.execute(
                    _MARK_SUPERSEDED,
                    {
                        "snapshot_id": snapshot_id,
                        "row_counts": row_counts,
                        "validation": {**validation, "superseded_by_newer": str(newer)},
                        "built_from_watermark": built_from_watermark,
                        "built_at": built_at,
                    },
                )
                return False
            connection.execute(
                _CLEAR_LATEST, {"deployment_id": deployment_id, "plane": plane}
            )
            connection.execute(
                _PUBLISH_SNAPSHOT,
                {
                    "snapshot_id": snapshot_id,
                    "row_counts": row_counts,
                    "validation": validation,
                    "built_from_watermark": built_from_watermark,
                    # The cut the export actually took, not the instant the
                    # registry row happened to be inserted.
                    "built_at": built_at,
                },
            )
        return True

    def purge_snapshot_prefixes(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> int:
        """Delete exact old registry rows after their clean replacements publish."""
        if not prefixes:
            return 0
        with self._engine.begin() as connection:
            deleted = connection.execute(
                _PURGE_SNAPSHOT_PREFIXES,
                {"deployment_id": deployment_id, "prefixes": list(prefixes)},
            ).rowcount
        return deleted or 0

    def snapshot_prefixes_exist(
        self, *, deployment_id: UUID, prefixes: tuple[str, ...]
    ) -> bool:
        """Return whether any manifest-nominated registry pointer remains."""
        if not prefixes:
            return False
        with self._engine.connect() as connection:
            return bool(
                connection.execute(
                    _SNAPSHOT_PREFIXES_EXIST,
                    {"deployment_id": deployment_id, "prefixes": list(prefixes)},
                ).scalar_one()
            )

    @contextmanager
    def corpus_export(self, *, deployment_id: UUID) -> Iterator["CorpusExport"]:
        """One consistent cut of the corpus (single REPEATABLE READ read).

        The tree's three inputs — documents, entities, and the mention
        links between them — must come from ONE snapshot: read separately,
        a deletion or re-resolution between them publishes a tree with a
        just-deleted document or an entity page missing its evidence
        (Codex review).
        """
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            yield CorpusExport(connection=connection, deployment_id=deployment_id)

    def corpus_documents(self, *, deployment_id: UUID) -> tuple[dict[str, object], ...]:
        """Every live lineage with its placement hint and root summary (P3).

        The member table's whole value is that an agent reads ONE index and
        learns what every file is about — so the root section's summary and
        title ride along, already stored by the structure stage (D39).
        """
        with self._engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    _SELECT_CORPUS_DOCUMENTS, {"deployment_id": deployment_id}
                ).mappings()
            )

    def corpus_entities(self, *, deployment_id: UUID) -> tuple[dict[str, object], ...]:
        """Active entities with their profile and mention reach (P3 tier 1)."""
        with self._engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    _SELECT_CORPUS_ENTITIES, {"deployment_id": deployment_id}
                ).mappings()
            )

    def entity_document_links(
        self, *, deployment_id: UUID
    ) -> tuple[dict[str, object], ...]:
        """Which documents evidence which entity (the entity page's members)."""
        with self._engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    _SELECT_ENTITY_DOCUMENTS, {"deployment_id": deployment_id}
                ).mappings()
            )

    def latest_snapshot(
        self, *, deployment_id: UUID, plane: str
    ) -> dict[str, object] | None:
        """The published snapshot readers should serve, if any."""
        _require_p3_plane(plane)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_LATEST, {"deployment_id": deployment_id, "plane": plane}
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None


def _require_p3_plane(plane: str) -> None:
    """Reject obsolete live-state planes at the P3-only snapshot catalog."""
    if plane != "P3_corpusfs":
        raise ValueError("projection snapshots are P3 CorpusFS only")


_INSERT_SNAPSHOT = text(
    """
    INSERT INTO projection_snapshots (
        snapshot_id, deployment_id, plane, version, gcs_uri, status
    ) VALUES (
        :snapshot_id, :deployment_id, CAST(:plane AS projection_plane),
        :version, :gcs_uri, 'building'
    )
    """
)

_MARK_FAILED = text(
    """
    UPDATE projection_snapshots
    SET status = 'failed', validation = :validation
    WHERE snapshot_id = :snapshot_id
    """
).bindparams(bindparam("validation", type_=JSON))

_CLEAR_LATEST = text(
    """
    UPDATE projection_snapshots
    SET is_latest = false, status = 'superseded'
    WHERE deployment_id = :deployment_id
      AND plane = CAST(:plane AS projection_plane)
      AND is_latest
    """
)

_PUBLISH_SNAPSHOT = text(
    """
    UPDATE projection_snapshots
    SET status = 'published', is_latest = true, row_counts = :row_counts,
        validation = :validation, built_from_watermark = :built_from_watermark,
        built_at = coalesce(:built_at, built_at),
        published_at = now()
    WHERE snapshot_id = :snapshot_id
    """
).bindparams(bindparam("row_counts", type_=JSON), bindparam("validation", type_=JSON))

_SELECT_CORPUS_DOCUMENTS = text(
    """
    SELECT d.doc_id, d.title, d.source_kind, d.source_ref, d.source_uri,
           v.version_id, v.content_hash, v.source_modified_at, v.published_at,
           r.markdown_uri, c.raw_uri, c.mime, s.summary AS root_summary,
           s.placement_path
    FROM documents d
    JOIN document_versions v ON v.version_id = d.current_version_id
    LEFT JOIN document_representations r
           ON r.representation_id = v.current_representation_id
    LEFT JOIN content_objects c
           ON c.deployment_id = v.deployment_id AND c.content_hash = v.content_hash
    LEFT JOIN document_sections s
           ON s.representation_id = r.representation_id
          AND s.structure_generation_id = r.current_structure_generation_id
          AND s.node_path = '0'
    WHERE d.deployment_id = :deployment_id AND d.deleted_at IS NULL
    ORDER BY d.doc_id
    """
)

_SELECT_CORPUS_ENTITIES = text(
    """
    SELECT e.entity_id, e.canonical_name, e.profile_summary,
           e.mention_count, e.graph_degree
    FROM entities e
    WHERE e.deployment_id = :deployment_id AND e.status = 'active'
    ORDER BY e.entity_id
    """
)

_SELECT_ENTITY_DOCUMENTS = text(
    """
    SELECT DISTINCT rd.entity_id, m.doc_id
    FROM mentions m
    JOIN resolution_decisions rd
      ON rd.mention_id = m.mention_id AND rd.superseded_by IS NULL
    JOIN documents d ON d.doc_id = m.doc_id AND d.deleted_at IS NULL
    WHERE m.deployment_id = :deployment_id
    ORDER BY rd.entity_id, m.doc_id
    """
)

_SELECT_LATEST = text(
    """
    SELECT snapshot_id, version, gcs_uri, row_counts, built_at, published_at
    FROM projection_snapshots
    WHERE deployment_id = :deployment_id
      AND plane = CAST(:plane AS projection_plane)
      AND is_latest
    """
)

_LOCK_PUBLISH = text(
    """
    SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))
    """
)

_PURGE_SNAPSHOT_PREFIXES = text(
    """
    DELETE FROM projection_snapshots
    WHERE deployment_id = :deployment_id
      AND gcs_uri = ANY(:prefixes)
    """
)

_SNAPSHOT_PREFIXES_EXIST = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM projection_snapshots
        WHERE deployment_id = :deployment_id AND gcs_uri = ANY(:prefixes)
    )
    """
)

_SELECT_NEWER_LATEST = text(
    """
    SELECT cur.snapshot_id
    FROM projection_snapshots cur, projection_snapshots mine
    WHERE cur.deployment_id = :deployment_id
      AND cur.plane = CAST(:plane AS projection_plane)
      AND cur.is_latest
      AND mine.snapshot_id = :snapshot_id
      -- Compare against the cut this candidate is ABOUT to record, not the
      -- registry-insert time it still carries: the row is stamped with its
      -- export cut by the publish below, and reading the stale value here
      -- superseded a genuinely newer snapshot.
      AND cur.built_at > coalesce(CAST(:built_at AS timestamptz), mine.built_at)
    """
)

_MARK_SUPERSEDED = text(
    """
    UPDATE projection_snapshots
    SET status = 'superseded', row_counts = :row_counts,
        validation = :validation, built_from_watermark = :built_from_watermark,
        built_at = coalesce(CAST(:built_at AS timestamptz), built_at)
    WHERE snapshot_id = :snapshot_id
    """
).bindparams(bindparam("row_counts", type_=JSON), bindparam("validation", type_=JSON))
