"""The E1 chunk catalog: chunk-row writes and stage loads (D56/D58 keys in PG).

Chunk text and vectors never land here (D37/D8): Postgres stores offsets,
section links, version stamps, and the reuse keys; bodies stay in the
artifacts store and vectors in the P1 index.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ChunkSourceNotFoundError
from rememberstack.model import EmbeddingUpdate
from rememberstack.model import EvidenceSpan
from rememberstack.model import TextOriginMatch
from rememberstack.spine.chunk_reuse import lock_chunk_lineage
from rememberstack.spine.chunk_reuse import read_committed
from rememberstack.spine.chunk_reuse import version_is_reusable


@dataclass(frozen=True, slots=True)
class EmbeddingReuse:
    """A missing occurrence and its exact current embedding identity."""

    chunk_content_hash: str
    body: str
    update: EmbeddingUpdate


class ChunkCatalog:
    """E1 row writes and stage loads over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def chunk_source(self, *, representation_id: UUID) -> ChunkSource:
        """Load what the chunk stage needs about one representation."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_CHUNK_SOURCE, {"representation_id": representation_id}
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ChunkSourceNotFoundError(
                    f"document representation {representation_id} does not exist"
                )
            sections = (
                connection.execute(
                    _SELECT_SECTIONS, {"representation_id": representation_id}
                )
                .mappings()
                .all()
            )
        return ChunkSource.model_validate(
            {**dict(row), "sections": tuple(dict(section) for section in sections)}
        )

    def existing_chunk_ids(
        self, *, representation_id: UUID, chunker_version: str
    ) -> tuple[UUID, ...]:
        """Chunks this generation already packed for the representation (D7 replay).

        Scoped by representation AND chunker generation: a re-conversion or a
        parameter change never replays rows cut from a different coordinate
        system or under different numbers.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_EXISTING_CHUNKS,
                {
                    "representation_id": representation_id,
                    "chunker_version": chunker_version,
                },
            ).scalars()
            return tuple(rows)

    def save_grid(
        self,
        *,
        source: ChunkSource,
        chunker_version: str,
        identities: tuple[str, ...],
        has_split: bool,
        build_records: Callable[[dict[str, TextOriginMatch]], tuple[ChunkRecord, ...]],
    ) -> None:
        """Freeze a complete grid and its text origins under the lineage lock."""
        with read_committed(self._engine) as connection:
            lock_chunk_lineage(
                connection=connection,
                deployment_id=source.deployment_id,
                doc_id=source.doc_id,
                exclusive=True,
            )
            if (
                connection.execute(
                    _SELECT_EXISTING_CHUNKS,
                    {
                        "representation_id": source.representation_id,
                        "chunker_version": chunker_version,
                    },
                ).first()
                is not None
            ):
                return
            version_date = source.source_modified_at or source.published_at
            matches = {}
            if version_date is not None and not has_split:
                matches = self._text_origin_matches_on(
                    connection=connection,
                    deployment_id=source.deployment_id,
                    doc_id=source.doc_id,
                    version_id=source.version_id,
                    reuse_identity_hashes=tuple(dict.fromkeys(identities)),
                    not_after=version_date,
                )
            for record in build_records(matches):
                connection.execute(_INSERT_CHUNK, record.model_dump(mode="json"))

    def text_origin_matches(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        reuse_identity_hashes: tuple[str, ...],
        not_after: datetime,
    ) -> dict[str, TextOriginMatch]:
        """Per identity, the earliest eligible text origin in the lineage (D140 §5).

        Eligible: same lineage and ``reuse_identity_hash``, a version that is
        not deleted now, and a known origin not later than ``not_after`` (the
        new version's own date). Ties break by version number, then ordinal.
        Chunks created before D140 carry no identity and never match.
        """
        with self._engine.connect() as connection:
            return self._text_origin_matches_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                reuse_identity_hashes=reuse_identity_hashes,
                not_after=not_after,
            )

    def _text_origin_matches_on(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        reuse_identity_hashes: tuple[str, ...],
        not_after: datetime,
    ) -> dict[str, TextOriginMatch]:
        if not reuse_identity_hashes:
            return {}
        rows = connection.execute(
            _SELECT_TEXT_ORIGIN_MATCHES,
            {
                "deployment_id": deployment_id,
                "doc_id": doc_id,
                "version_id": version_id,
                "hashes": list(reuse_identity_hashes),
                "not_after": not_after,
            },
        ).mappings()
        return {
            row["reuse_identity_hash"]: TextOriginMatch.model_validate(dict(row))
            for row in rows
        }

    def chunks_for_embedding(
        self, *, representation_id: UUID, chunker_version: str
    ) -> tuple[ChunkForEmbedding, ...]:
        """Load one (representation, generation)'s chunk rows with their signals."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_FOR_EMBEDDING,
                    {
                        "representation_id": representation_id,
                        "chunker_version": chunker_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            ChunkForEmbedding.model_validate(_normalize_chunk_embed_row(dict(row)))
            for row in rows
        )

    def representation_id_for_chunk(self, *, chunk_id: UUID) -> UUID | None:
        """The representation that owns this chunk row, if it still exists."""
        with self._engine.connect() as connection:
            value = connection.execute(
                _SELECT_CHUNK_REPRESENTATION, {"chunk_id": chunk_id}
            ).scalar_one_or_none()
        return None if value is None else UUID(str(value))

    def chunks_for_extract(
        self, *, representation_id: UUID, chunker_version: str, chunk_id: UUID
    ) -> tuple[ChunkForEmbedding, ...]:
        """D84: load only the target chunk and same-section prev/next neighbours.

        Claimify's bundle uses at most ±1 same-section neighbour. Loading the
        full grid per chunk job is O(N²) work on large documents.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_FOR_EXTRACT_WINDOW,
                    {
                        "representation_id": representation_id,
                        "chunker_version": chunker_version,
                        "chunk_id": chunk_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            ChunkForEmbedding.model_validate(_normalize_chunk_embed_row(dict(row)))
            for row in rows
        )

    def chunks_for_references(
        self, *, representation_id: UUID, chunker_version: str, chunk_id: UUID
    ) -> tuple[ChunkForEmbedding, ...]:
        """Load target, eight preceding producers and their possible neighbor support."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_FOR_REFERENCE_WINDOW,
                    {
                        "representation_id": representation_id,
                        "chunker_version": chunker_version,
                        "chunk_id": chunk_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            ChunkForEmbedding.model_validate(_normalize_chunk_embed_row(dict(row)))
            for row in rows
        )

    def list_chunk_ids(
        self, *, representation_id: UUID, chunker_version: str
    ) -> tuple[UUID, ...]:
        """Ordered chunk ids for one packing generation (fan-out without full rows)."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_EXISTING_CHUNKS,
                {
                    "representation_id": representation_id,
                    "chunker_version": chunker_version,
                },
            ).all()
        return tuple(UUID(str(row[0])) for row in rows)

    def split_windows_for_spans(
        self,
        *,
        source: ChunkSource,
        chunker_version: str,
        spans: tuple[EvidenceSpan, ...],
    ) -> tuple[tuple[int, int], ...]:
        """Find split chunks in this grid that contain cited passages."""
        if not spans:
            return ()
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_CITED_SPLIT_WINDOWS,
                {
                    "deployment_id": source.deployment_id,
                    "version_id": source.version_id,
                    "representation_id": source.representation_id,
                    "chunker_version": chunker_version,
                    "starts": [span.char_start for span in spans],
                    "ends": [span.char_end for span in spans],
                },
            )
            return tuple((row.char_start, row.char_end) for row in rows)

    def replay_or_copy_embeddings(
        self,
        *,
        source: ChunkSource,
        candidates: tuple[EmbeddingReuse, ...],
        require_nonzero: bool = False,
    ) -> set[UUID]:
        """Replay stamped vectors or copy and stamp donors under the lineage lock."""
        if not candidates:
            return set()
        active = candidates[0].update
        with read_committed(self._engine) as connection:
            lock_chunk_lineage(
                connection=connection,
                deployment_id=source.deployment_id,
                doc_id=source.doc_id,
                exclusive=False,
            )
            complete = set(
                connection.execute(
                    _SELECT_STAMPED_EMBEDDINGS,
                    {
                        "deployment_id": source.deployment_id,
                        "policy_generation": active.policy_generation,
                        "embedding_version": active.embedding_version,
                        "chunk_ids": [item.update.chunk_id for item in candidates],
                        "input_hashes": [
                            item.update.embedding_text_hash for item in candidates
                        ],
                        "require_nonzero": require_nonzero,
                    },
                ).scalars()
            )
            missing = tuple(
                item for item in candidates if item.update.chunk_id not in complete
            )
            if not missing:
                return complete
            carry = self.carry_forward_sources(
                connection=connection, source=source, candidates=missing
            )
            for candidate in missing:
                update = candidate.update
                donor = carry.get(
                    (candidate.chunk_content_hash, update.embedding_text_hash)
                )
                if donor is None or donor == update.chunk_id:
                    continue
                inserted = connection.execute(
                    _COPY_EMBEDDING,
                    {
                        "deployment_id": source.deployment_id,
                        "donor": donor,
                        "target": update.chunk_id,
                        "search_text": candidate.body,
                        "policy_generation": active.policy_generation,
                        "embedding_version": active.embedding_version,
                        "embedding_text_hash": update.embedding_text_hash,
                        "require_nonzero": require_nonzero,
                    },
                ).rowcount
                if inserted:
                    connection.execute(
                        _UPDATE_EMBEDDING, update.model_dump(mode="json")
                    )
                    complete.add(update.chunk_id)
        return complete

    def carry_forward_sources(
        self,
        *,
        connection: Connection,
        source: ChunkSource,
        candidates: tuple[EmbeddingReuse, ...],
    ) -> dict[tuple[str, str], UUID]:
        """Rank donors after checking both versions under the shared lock."""
        active = candidates[0].update
        rows = connection.execute(
            _SELECT_CARRY_FORWARD,
            {
                "deployment_id": source.deployment_id,
                "doc_id": source.doc_id,
                "version_id": source.version_id,
                "policy_generation": active.policy_generation,
                "embedding_version": active.embedding_version,
                "content_hashes": [
                    candidate.chunk_content_hash for candidate in candidates
                ],
                "input_hashes": [
                    candidate.update.embedding_text_hash for candidate in candidates
                ],
            },
        ).mappings()
        return {
            (row["chunk_content_hash"], row["embedding_text_hash"]): row["chunk_id"]
            for row in rows
        }

    def record_embeddings(self, *, updates: tuple[EmbeddingUpdate, ...]) -> None:
        """Write the embed stage's refs, D80 stamps, and version fields back."""
        if not updates:
            return
        with self._engine.begin() as connection:
            for update in updates:
                connection.execute(_UPDATE_EMBEDDING, update.model_dump(mode="json"))


def _normalize_chunk_embed_row(row: dict) -> dict:
    """Coerce JSONB and optional D80 fields for ChunkForEmbedding."""
    facts = row.get("location_facts_json")
    if facts is not None and not isinstance(facts, str):
        import json

        row["location_facts_json"] = json.dumps(facts)
    return row


_SELECT_CHUNK_SOURCE = text(
    """
    SELECT r.deployment_id, v.doc_id, r.version_id, r.representation_id,
           r.markdown_uri, r.blocks_uri, r.conversion_uri, d.title,
           -- D134: the name and title recorded for THIS version (a rename
           -- is a new version with a new file name); the lineage title
           -- above keeps its meaning for embeddings and structure.
           m.file_name, m.title AS version_title, d.source_kind,
           v.source_modified_at, v.published_at, v.language,
           r.structurer_version, r.blockizer_version,
           coalesce(v.source_shape, 'document') AS source_shape,
           v.channel_ref, v.thread_ref, v.author_ref, v.message_ts
    FROM document_representations r
    JOIN document_versions v ON v.version_id = r.version_id
    JOIN documents d ON d.doc_id = v.doc_id
    LEFT JOIN document_metadata m
      ON m.deployment_id = v.deployment_id AND m.version_id = v.version_id
    WHERE r.representation_id = :representation_id
    """
)

_SELECT_SECTIONS = text(
    """
    SELECT s.section_id, s.node_path, s.role, s.block_start, s.block_end,
           s.summary, s.title
    FROM document_sections s
    JOIN document_representations r
      ON r.representation_id = s.representation_id
     AND r.current_structure_generation_id = s.structure_generation_id
    WHERE s.representation_id = :representation_id
    ORDER BY string_to_array(node_path, '.')::int[]
    """
)

_SELECT_EXISTING_CHUNKS = text(
    """
    SELECT chunk_id FROM chunks
    WHERE representation_id = :representation_id
      AND chunker_version = :chunker_version
    ORDER BY ordinal
    """
)

_INSERT_CHUNK = text(
    """
    INSERT INTO chunks (
        chunk_id, deployment_id, doc_id, version_id, representation_id,
        section_id, ordinal, block_start, block_end, chunk_content_hash,
        extraction_input_hash, char_start, char_end, token_count,
        chunker_version, extraction_eligible, extraction_eligibility_version,
        reuse_identity_hash, text_origin_at, span_kind
    ) VALUES (
        :chunk_id, :deployment_id, :doc_id, :version_id, :representation_id,
        :section_id, :ordinal, :block_start, :block_end, :chunk_content_hash,
        :extraction_input_hash, :char_start, :char_end, :token_count,
        :chunker_version, :extraction_eligible, :extraction_eligibility_version,
        :reuse_identity_hash, CAST(:text_origin_at AS timestamptz),
        CAST(:span_kind AS chunk_span_kind)
    )
    """
)

_SELECT_TEXT_ORIGIN_MATCHES = text(
    f"""
    SELECT DISTINCT ON (c.reuse_identity_hash)
           c.reuse_identity_hash, c.text_origin_at, v.version_no, c.ordinal
    FROM chunks c
    JOIN document_versions v ON v.version_id = c.version_id
    WHERE c.deployment_id = :deployment_id
      AND c.doc_id = :doc_id
      AND c.reuse_identity_hash = ANY(:hashes)
      AND c.text_origin_at IS NOT NULL
      AND c.text_origin_at <= :not_after
      AND v.deleted_at IS NULL
      AND {version_is_reusable(":version_id")}
      AND {version_is_reusable("c.version_id")}
    ORDER BY c.reuse_identity_hash, c.text_origin_at, v.version_no, c.ordinal
    """
)

_SELECT_FOR_EMBEDDING = text(
    """
    SELECT c.chunk_id, c.doc_id, c.version_id, c.ordinal,
           c.char_start, c.char_end, c.context_prefix, c.prefixer_version,
           c.location_header, c.embedding_text_hash,
           c.embedding_input_policy_version, c.policy_generation,
           c.embedding_ref, c.embedding_version, c.location_facts_json,
           c.chunk_content_hash, c.extraction_input_hash, c.section_id,
           c.extraction_eligible, c.text_origin_at, c.span_kind,
           s.role AS section_role, s.node_path AS section_path,
           s.title AS section_title
    FROM chunks c
    JOIN document_sections s ON s.section_id = c.section_id
    WHERE c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
    ORDER BY c.ordinal
    """
)

_SELECT_CHUNK_REPRESENTATION = text(
    """
    SELECT representation_id FROM chunks WHERE chunk_id = :chunk_id
    """
)

_SELECT_FOR_EXTRACT_WINDOW = text(
    """
    WITH target AS (
        SELECT c.*, s.role AS section_role, s.node_path AS section_path,
               s.title AS section_title
        FROM chunks c
        JOIN document_sections s ON s.section_id = c.section_id
        WHERE c.chunk_id = :chunk_id
          AND c.representation_id = :representation_id
          AND c.chunker_version = :chunker_version
    ),
    windowed AS (
        SELECT c.chunk_id, c.doc_id, c.version_id, c.ordinal,
               c.char_start, c.char_end, c.context_prefix, c.prefixer_version,
               c.location_header, c.embedding_text_hash,
               c.embedding_input_policy_version, c.policy_generation,
               c.embedding_ref, c.embedding_version, c.location_facts_json,
               c.chunk_content_hash, c.extraction_input_hash, c.section_id,
               c.extraction_eligible, c.text_origin_at, c.span_kind,
               s.role AS section_role, s.node_path AS section_path,
               s.title AS section_title
        FROM chunks c
        JOIN document_sections s ON s.section_id = c.section_id
        JOIN target t ON c.representation_id = t.representation_id
         AND c.chunker_version = t.chunker_version
         AND s.node_path = t.section_path
        WHERE c.ordinal = t.ordinal
           OR c.ordinal = (
                SELECT max(c2.ordinal) FROM chunks c2
                JOIN document_sections s2 ON s2.section_id = c2.section_id
                WHERE c2.representation_id = t.representation_id
                  AND c2.chunker_version = t.chunker_version
                  AND s2.node_path = t.section_path
                  AND c2.ordinal < t.ordinal
              )
           OR c.ordinal = (
                SELECT min(c2.ordinal) FROM chunks c2
                JOIN document_sections s2 ON s2.section_id = c2.section_id
                WHERE c2.representation_id = t.representation_id
                  AND c2.chunker_version = t.chunker_version
                  AND s2.node_path = t.section_path
                  AND c2.ordinal > t.ordinal
              )
    )
    SELECT * FROM windowed
    ORDER BY ordinal
    """
)

_SELECT_STAMPED_EMBEDDINGS = text("""
    SELECT c.chunk_id
    FROM unnest(CAST(:chunk_ids AS uuid[]), CAST(:input_hashes AS text[]))
         AS expected(chunk_id, embedding_text_hash)
    JOIN chunks c ON c.chunk_id = expected.chunk_id
    JOIN chunk_search p ON p.deployment_id = c.deployment_id AND p.chunk_id = c.chunk_id
    WHERE c.deployment_id = :deployment_id
      AND c.embedding_ref NOT LIKE 'skip:%'
      AND c.policy_generation = :policy_generation
      AND c.embedding_version = :embedding_version
      AND c.embedding_text_hash = expected.embedding_text_hash
      AND p.embedding_input_policy_version = c.policy_generation
      AND p.embedding_model = c.embedding_version
      AND p.embedding_text_hash = c.embedding_text_hash
      AND p.embedding IS NOT NULL
      AND (NOT :require_nonzero OR vector_norm(p.embedding) > 0)
""")

_SELECT_CARRY_FORWARD = text(
    f"""
    SELECT DISTINCT ON (c.chunk_content_hash, c.embedding_text_hash)
           c.chunk_content_hash, c.chunk_id, c.embedding_text_hash
    FROM chunks c
    JOIN document_versions cv ON cv.version_id = c.version_id
    WHERE
      (c.chunk_content_hash, c.embedding_text_hash) IN (
        SELECT * FROM unnest(CAST(:content_hashes AS text[]), CAST(:input_hashes AS text[]))
      )
      AND c.deployment_id = :deployment_id
      AND c.doc_id = :doc_id
      AND cv.version_no <= (SELECT version_no FROM document_versions
                           WHERE version_id = :version_id)
      AND c.embedding_text_hash IS NOT NULL
      AND cv.deleted_at IS NULL
      AND c.policy_generation = :policy_generation
      AND c.embedding_version = :embedding_version
      AND c.embedding_ref IS NOT NULL
      AND {version_is_reusable(":version_id")}
      AND {version_is_reusable("c.version_id")}
    ORDER BY c.chunk_content_hash, c.embedding_text_hash, cv.version_no DESC, c.ordinal, c.chunk_id
    """
)

_COPY_EMBEDDING = text("""
    INSERT INTO chunk_search (
        deployment_id, chunk_id, search_text, embedding, embedding_model,
        embedding_input_policy_version, embedding_text_hash
    )
    SELECT deployment_id, :target, :search_text, embedding, embedding_model,
           embedding_input_policy_version, embedding_text_hash
    FROM chunk_search
    WHERE deployment_id=:deployment_id AND chunk_id=:donor AND embedding IS NOT NULL
      AND (NOT :require_nonzero OR vector_norm(embedding) > 0)
      AND embedding_input_policy_version=:policy_generation
      AND embedding_model=:embedding_version AND embedding_text_hash=:embedding_text_hash
    ON CONFLICT (deployment_id, chunk_id) DO UPDATE SET
        search_text=EXCLUDED.search_text, embedding=EXCLUDED.embedding,
        embedding_model=EXCLUDED.embedding_model,
        embedding_input_policy_version=EXCLUDED.embedding_input_policy_version,
        embedding_text_hash=EXCLUDED.embedding_text_hash
""")


_UPDATE_EMBEDDING = text(
    """
    UPDATE chunks
    SET embedding_ref = :embedding_ref,
        embedding_version = :embedding_version,
        location_header = :location_header,
        embedding_text_hash = :embedding_text_hash,
        embedding_input_policy_version = :embedding_input_policy_version,
        policy_generation = :policy_generation,
        location_facts_json = CAST(:location_facts_json AS jsonb),
        context_prefix = :context_prefix,
        prefixer_version = :prefixer_version
    WHERE chunk_id = :chunk_id
    """
)


_SELECT_FOR_REFERENCE_WINDOW = text(
    str(_SELECT_FOR_EMBEDDING).replace(
        "ORDER BY c.ordinal",
        """AND c.ordinal BETWEEN
             (SELECT ordinal-9 FROM chunks WHERE chunk_id=:chunk_id) AND
             (SELECT ordinal+1 FROM chunks WHERE chunk_id=:chunk_id)
           ORDER BY c.ordinal""",
    )
)

_SELECT_CITED_SPLIT_WINDOWS = text("""
    SELECT c.char_start, c.char_end
    FROM chunks c
    WHERE c.deployment_id = :deployment_id AND c.version_id = :version_id
      AND c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version AND c.span_kind = 'split'
      AND EXISTS (
          SELECT 1 FROM unnest(CAST(:starts AS integer[]), CAST(:ends AS integer[]))
               AS cited(char_start, char_end)
          WHERE c.char_start <= cited.char_start AND cited.char_end <= c.char_end
      )
""")
