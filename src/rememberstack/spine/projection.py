"""The P2 projection catalog (D7/D44): snapshot registry + the export reads.

Spine-owned SQL for the rebuild-first graph pipeline (p2 §5). The
`projection_snapshots` registry is the pointer readers follow (`is_latest`,
one per deployment/plane — the object store holds only immutable snapshot
bytes, never a mutable pointer). The export executes the spike battery's
bound strategy: the survivor map materializes ONCE into an indexed temp
table per export connection, and every edge read joins against it — the
`memory_v1` invariant views are the semantic export contract. The catalog
retains the indexed survivor map only for the corruption/cycle abort gate
(`plan/analysis/p2_spike_battery.md`, finding 2).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Final
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy import TextClause
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import Row

GRAPH_NODE_TABLES: Final = ("Entity", "Document")
GRAPH_REL_TABLES: Final = ("RELATES", "MENTIONED_IN", "DOC_CROSSREF", "IS_DOCUMENT")
"""Load order is binding: every node table before any rel table (COPY-REL
resolves endpoints against node PKs and throws on a missing endpoint)."""


class GraphExport:
    """One deployment's invariant-filtered export on a consistent connection."""

    def __init__(self, *, connection: Connection, deployment_id: UUID) -> None:
        """Bind to the export connection (the temp survivor table exists)."""
        self._connection = connection
        self._deployment_id = deployment_id
        # §3.5 binds `built_at` to THIS transaction's timestamp, captured once
        # at export start. It is the instant the whole snapshot is a consistent
        # cut of, and it is what every answer from that snapshot is scoped to —
        # so it comes from the transaction that took the cut, never from a
        # row-insert default or a wall clock read at publish or query time.
        self._built_at = connection.execute(
            text("SELECT transaction_timestamp()")
        ).scalar_one()

    @property
    def built_at(self) -> datetime:
        """The export transaction's timestamp: the cut this snapshot projects."""
        return self._built_at

    def rows(self, *, table: str) -> Iterator[Row]:
        """Stream one graph table's rows (server-side cursor)."""
        statement = _EXPORT_SQL[table]
        return iter(
            self._connection.execution_options(yield_per=10_000).execute(
                statement, {"deployment_id": self._deployment_id}
            )
        )

    def count(self, *, table: str) -> int:
        """The export-side row count (the validation gate's expectation)."""
        statement = _EXPORT_SQL[table]
        return int(
            self._connection.execute(
                text(f"SELECT count(*) FROM ({statement.text}) export"),  # noqa: S608
                {"deployment_id": self._deployment_id},
            ).scalar_one()
        )

    def watermark(self) -> object:
        """The max ingested_at INSIDE this export's snapshot (D7 bound).

        Read on the export connection, so it can never advertise a relation
        the consistent cut cannot contain.
        """
        return self._connection.execute(
            _SELECT_WATERMARK, {"deployment_id": self._deployment_id}
        ).scalar_one_or_none()

    def unresolved_survivors(self) -> tuple[UUID, ...]:
        """Return entities omitted by terminal survivor resolution.

        A cycle or dangling redirect has no terminal row in ``graph_survivor``.
        Any omission aborts the snapshot and is recorded for the operator.
        """
        return tuple(
            self._connection.execute(
                _SELECT_UNRESOLVED_SURVIVORS, {"deployment_id": self._deployment_id}
            ).scalars()
        )


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
        """Active entities with their profile and reach (P3 tier 1)."""
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
    """Snapshot registry rows and the graph export reads."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    @contextmanager
    def graph_export(self, *, deployment_id: UUID) -> Iterator[GraphExport]:
        """One consistent export pass (single transaction, survivor map once).

        Everything the snapshot reads happens inside one REPEATABLE READ
        transaction — the snapshot is a consistent cut of Postgres, and the
        indexed temp survivor table keeps every edge join linear.
        """
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            connection.execute(_CREATE_SURVIVOR_MAP, {"deployment_id": deployment_id})
            connection.execute(_INDEX_SURVIVOR_MAP)
            try:
                yield GraphExport(connection=connection, deployment_id=deployment_id)
            finally:
                connection.rollback()  # temp table + snapshot cut end together

    def open_snapshot(
        self, *, deployment_id: UUID, plane: str, version: str, store_prefix: str
    ) -> UUID:
        """Register one building snapshot."""
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
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_PUBLISH, {"key": f"p2-publish:{deployment_id}:{plane}"}
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

    def record_graph_analytics(
        self,
        *,
        deployment_id: UUID,
        snapshot_id: UUID,
        communities: tuple[dict[str, object], ...],
        metrics: tuple[dict[str, object], ...],
        detector_version: str,
        label_model: str | None = None,
    ) -> None:
        """Write one rebuild's analytics back to Postgres (D6/D11/D72).

        The graph stays a projection: PageRank, k-core, WCC, and community
        membership are graph-DERIVED, so they land here and are never
        reprojected into the node tables (that would be circular). Both
        tables are snapshot-scoped and cascade with it, so a re-run of the
        same snapshot replaces its own rows rather than accumulating.
        """
        with self._engine.begin() as connection:
            # the detector generation is registered like every other
            # component (D12): an algorithm or label-model change is
            # traceable to the assignments it produced
            connection.execute(
                _REGISTER_DETECTOR,
                {
                    "deployment_id": deployment_id,
                    "version": detector_version,
                    "model_name": label_model,
                },
            )
            connection.execute(_CLEAR_METRICS, {"snapshot_id": snapshot_id})
            connection.execute(_CLEAR_COMMUNITIES, {"snapshot_id": snapshot_id})
            for community in communities:
                connection.execute(
                    _INSERT_COMMUNITY,
                    {"deployment_id": deployment_id, "snapshot_id": snapshot_id}
                    | community,
                )
            for metric in metrics:
                connection.execute(
                    _INSERT_METRIC,
                    {"deployment_id": deployment_id, "snapshot_id": snapshot_id}
                    | metric,
                )

    def collect_superseded_analytics(
        self, *, deployment_id: UUID, keep_snapshot_id: UUID
    ) -> int:
        """Drop analytics belonging to snapshots that are no longer current.

        The schema's contract: these rows are GC'd when their snapshot is
        superseded (they are per-snapshot derived state, not history). At a
        rebuild cadence they would otherwise accumulate one row per entity
        per cycle forever (Codex review). Returns how many rows were freed.
        """
        with self._engine.begin() as connection:
            metrics = connection.execute(
                _GC_METRICS, {"deployment_id": deployment_id, "keep": keep_snapshot_id}
            ).rowcount
            communities = connection.execute(
                _GC_COMMUNITIES,
                {"deployment_id": deployment_id, "keep": keep_snapshot_id},
            ).rowcount
        return (metrics or 0) + (communities or 0)

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

    def refresh_entity_degrees(self, *, deployment_id: UUID) -> None:
        """Copy degree from the PUBLISHED snapshot into `entities` (blast radius).

        Only the `is_latest` snapshot feeds this cache — a superseded or
        failed rebuild must never move the registry's blast-radius input.
        """
        with self._engine.begin() as connection:
            connection.execute(_REFRESH_DEGREES, {"deployment_id": deployment_id})

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
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_LATEST, {"deployment_id": deployment_id, "plane": plane}
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None


_CREATE_SURVIVOR_MAP = text(
    """
    CREATE TEMP TABLE graph_survivor ON COMMIT DROP AS
    SELECT s.entity_id, s.survivor
    FROM v_graph_survivor AS s
    JOIN entities AS e
      ON e.entity_id = s.entity_id
     AND e.deployment_id = :deployment_id
    """
)

_INDEX_SURVIVOR_MAP = text("CREATE INDEX ON graph_survivor (entity_id)")

_EXPORT_SQL: Final[dict[str, TextClause]] = {
    "Entity": text(
        """
        SELECT entity_id AS id, entity_type AS type,
               canonical_name AS name, normalized_name,
               profile_summary AS summary,
               (created_at AT TIME ZONE 'UTC') AS created_at
        FROM memory_v1.entities_current
        WHERE deployment_id = :deployment_id
        """
    ),
    "Document": text(
        """
        SELECT doc_id AS id, title, source_uri,
               (published_at AT TIME ZONE 'UTC')::date AS published_at
        FROM memory_v1.documents_live
        WHERE deployment_id = :deployment_id
        """
    ),
    "RELATES": text(
        """
        SELECT r.subject_entity_id AS from_id, r.object_entity_id AS to_id,
               r.relation_id, r.subject_entity_id AS subject_id,
               r.object_entity_id AS object_id,
               r.predicate, r.fact_label AS fact,
               r.evidence_count_current AS evidence_count,
               r.contradict_count_current AS contradict_count,
               r.confidence::float8 AS confidence, r.contradiction_group,
               (r.valid_from AT TIME ZONE 'UTC') AS valid_from,
               (r.valid_until AT TIME ZONE 'UTC') AS valid_until,
               (r.ingested_at AT TIME ZONE 'UTC') AS ingested_at,
               (r.invalidated_at AT TIME ZONE 'UTC') AS invalidated_at
        FROM memory_v1.graph_edges_visible_history AS r
        WHERE r.deployment_id = :deployment_id
        """
    ),
    "MENTIONED_IN": text(
        """
        SELECT entity_id AS from_id, doc_id AS to_id, mention_count,
               (first_mentioned_at AT TIME ZONE 'UTC') AS first_seen
        FROM memory_v1.entity_document_mentions
        WHERE deployment_id = :deployment_id
        """
    ),
    "DOC_CROSSREF": text(
        """
        SELECT from_doc_id AS from_id, to_doc_id AS to_id,
               from_doc_id, to_doc_id, kind, context
        FROM memory_v1.document_crossrefs_live
        WHERE deployment_id = :deployment_id
        """
    ),
    "IS_DOCUMENT": text(
        """
        SELECT entity.entity_id AS from_id, document.doc_id AS to_id
        FROM memory_v1.documents_live AS document
        JOIN documents AS raw
          ON raw.deployment_id = document.deployment_id
         AND raw.doc_id = document.doc_id
        JOIN v_memory_entity_survivor AS survivor
          ON survivor.deployment_id = raw.deployment_id
         AND survivor.entity_id = raw.document_entity_id
        JOIN memory_v1.entities_current AS entity
          ON entity.deployment_id = survivor.deployment_id
         AND entity.entity_id = survivor.survivor_entity_id
        WHERE document.deployment_id = :deployment_id
        """
    ),
}

_SELECT_UNRESOLVED_SURVIVORS = text(
    """
    SELECT e.entity_id
    FROM entities AS e
    LEFT JOIN graph_survivor AS resolved
      ON resolved.entity_id = e.entity_id
    WHERE e.deployment_id = :deployment_id
      AND resolved.entity_id IS NULL
    ORDER BY e.entity_id
    """
)

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

_CLEAR_METRICS = text(
    "DELETE FROM entity_graph_metrics WHERE snapshot_id = :snapshot_id"
)

_CLEAR_COMMUNITIES = text("DELETE FROM communities WHERE snapshot_id = :snapshot_id")

_INSERT_COMMUNITY = text(
    """
    INSERT INTO communities (
        community_id, deployment_id, snapshot_id, label, size, algorithm
    ) VALUES (
        :community_id, :deployment_id, :snapshot_id, :label, :size,
        CAST(:algorithm AS community_algorithm)
    )
    """
)

_INSERT_METRIC = text(
    """
    INSERT INTO entity_graph_metrics (
        deployment_id, entity_id, snapshot_id, community_id, pagerank,
        degree, k_core, component_id
    ) VALUES (
        :deployment_id, :entity_id, :snapshot_id, :community_id, :pagerank,
        :degree, :k_core, :component_id
    )
    """
)

_REGISTER_DETECTOR = text(
    """
    INSERT INTO pipeline_component_versions (
        deployment_id, component, version, model_name
    ) VALUES (
        :deployment_id, 'community_detector', :version, :model_name
    )
    ON CONFLICT (deployment_id, component, version) DO NOTHING
    """
)

_GC_METRICS = text(
    """
    DELETE FROM entity_graph_metrics
    WHERE deployment_id = :deployment_id AND snapshot_id <> :keep
    """
)

_GC_COMMUNITIES = text(
    """
    DELETE FROM communities
    WHERE deployment_id = :deployment_id AND snapshot_id <> :keep
    """
)

_REFRESH_DEGREES = text(
    """
    UPDATE entities e
    SET graph_degree = m.degree, updated_at = now()
    FROM entity_graph_metrics m
    JOIN projection_snapshots s ON s.snapshot_id = m.snapshot_id
    WHERE m.entity_id = e.entity_id
      AND m.deployment_id = :deployment_id
      AND s.is_latest
      AND s.plane = 'P2_graph'
      AND e.graph_degree IS DISTINCT FROM m.degree
    """
)

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
    SELECT e.entity_id, e.type, e.canonical_name, e.profile_summary,
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

_SELECT_WATERMARK = text(
    """
    SELECT max(ingested_at)
    FROM memory_v1.graph_edges_visible_history
    WHERE deployment_id = :deployment_id
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
        validation = :validation, built_from_watermark = :built_from_watermark
    WHERE snapshot_id = :snapshot_id
    """
).bindparams(bindparam("row_counts", type_=JSON), bindparam("validation", type_=JSON))
