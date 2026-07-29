"""The E1 chunk catalog: chunk-row writes and stage loads (D56/D58 keys in PG).

Chunk text and vectors never land here (D37/D8): Postgres stores offsets,
section links, version stamps, and the reuse keys; bodies stay in the
artifacts store and vectors in the P1 index.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import CarryForwardSource
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ChunkSourceNotFoundError
from rememberstack.model import EmbeddingUpdate


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

    def record_chunks(self, *, records: tuple[ChunkRecord, ...]) -> None:
        """Insert one packing run's chunk rows in one transaction."""
        if not records:
            return
        with self._engine.begin() as connection:
            for record in records:
                connection.execute(_INSERT_CHUNK, record.model_dump(mode="json"))

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
        return tuple(ChunkForEmbedding.model_validate(dict(row)) for row in rows)

    def carry_forward_sources(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        prefixer_version: str,
        embedding_version: str,
    ) -> dict[str, CarryForwardSource]:
        """Prior chunks of this lineage reusable by content hash (D56/A3).

        For each content hash: the nearest STRICTLY EARLIER version's chunk
        that already carries a stored prefix of the same prefixer generation
        and an embedding of the same embedding generation — the carry-forward
        source for an unchanged chunk in the new version. Earlier-only keeps
        version ancestry honest (a queued v2 never adopts a fast v3's
        context); duplicate identical chunks within one source version pick
        deterministically (lowest ordinal), and prefix + vector always copy
        from the SAME source row, so the indexed text and its vector agree.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_CARRY_FORWARD,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "version_id": version_id,
                        "prefixer_version": prefixer_version,
                        "embedding_version": embedding_version,
                    },
                )
                .mappings()
                .all()
            )
        return {
            row["chunk_content_hash"]: CarryForwardSource(
                chunk_id=row["chunk_id"], context_prefix=row["context_prefix"]
            )
            for row in rows
        }

    def record_embeddings(self, *, updates: tuple[EmbeddingUpdate, ...]) -> None:
        """Write the embed stage's refs, prefixes, and version stamps back."""
        if not updates:
            return
        with self._engine.begin() as connection:
            for update in updates:
                connection.execute(_UPDATE_EMBEDDING, update.model_dump(mode="json"))


_SELECT_CHUNK_SOURCE = text(
    """
    SELECT r.deployment_id, v.doc_id, r.version_id, r.representation_id,
           r.markdown_uri, r.blocks_uri, d.title, d.source_kind,
           v.source_modified_at, v.published_at, v.language,
           r.structurer_version
    FROM document_representations r
    JOIN document_versions v ON v.version_id = r.version_id
    JOIN documents d ON d.doc_id = v.doc_id
    WHERE r.representation_id = :representation_id
    """
)

_SELECT_SECTIONS = text(
    """
    SELECT s.section_id, s.node_path, s.role, s.block_start, s.block_end,
           s.summary
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
        chunker_version
    ) VALUES (
        :chunk_id, :deployment_id, :doc_id, :version_id, :representation_id,
        :section_id, :ordinal, :block_start, :block_end, :chunk_content_hash,
        :extraction_input_hash, :char_start, :char_end, :token_count,
        :chunker_version
    )
    """
)

_SELECT_FOR_EMBEDDING = text(
    """
    SELECT c.chunk_id, c.doc_id, c.version_id, c.ordinal,
           c.char_start, c.char_end, c.context_prefix, c.prefixer_version,
           c.chunk_content_hash, c.extraction_input_hash, c.section_id,
           s.role AS section_role, s.node_path AS section_path
    FROM chunks c
    JOIN document_sections s ON s.section_id = c.section_id
    WHERE c.representation_id = :representation_id
      AND c.chunker_version = :chunker_version
    ORDER BY c.ordinal
    """
)

_SELECT_CARRY_FORWARD = text(
    """
    SELECT DISTINCT ON (c.chunk_content_hash)
           c.chunk_content_hash, c.chunk_id, c.context_prefix
    FROM chunks c
    JOIN document_versions cv ON cv.version_id = c.version_id
    WHERE c.deployment_id = :deployment_id
      AND c.doc_id = :doc_id
      AND cv.version_no < (SELECT version_no FROM document_versions
                           WHERE version_id = :version_id)
      AND c.context_prefix IS NOT NULL
      AND c.prefixer_version = :prefixer_version
      AND c.embedding_version = :embedding_version
      AND c.embedding_ref IS NOT NULL
    ORDER BY c.chunk_content_hash, cv.version_no DESC, c.ordinal
    """
)

_UPDATE_EMBEDDING = text(
    """
    UPDATE chunks
    SET embedding_ref = :embedding_ref,
        embedding_version = :embedding_version,
        context_prefix = :context_prefix,
        prefixer_version = :prefixer_version
    WHERE chunk_id = :chunk_id
    """
)
