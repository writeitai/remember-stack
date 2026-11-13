"""Read-only D155 selection. Publication and the existing P3 profile are separate."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.corpus_export import CorpusDocument


class CorpusReaderCatalog:
    """Stream the served readings in one repeatable-read database snapshot."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def documents(
        self, *, deployment_id: UUID, doc_id: UUID | None = None
    ) -> Iterator[Iterator[CorpusDocument]]:
        """Select current pointers, not newest stored or candidate generations.

        The pointers already encode completion under enabled layers (D143).
        Missing immutable inventories are errors in the reader, not a reason to
        silently present a raw-only or partial folder.
        """
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            statement = _DOCUMENTS + (" AND d.doc_id = :doc_id" if doc_id else "")
            rows = (
                connection.execution_options(yield_per=150)
                .execute(
                    text(statement + " ORDER BY d.doc_id"),
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                )
                .mappings()
            )
            try:
                yield (CorpusDocument(**dict(row)) for row in rows)
            finally:
                rows.close()


_DOCUMENTS = """
SELECT d.deployment_id, d.doc_id, d.source_kind, d.source_ref, d.source_uri,
       d.title AS lineage_title, d.corpus_published_revision,
       v.version_id, v.version_no, v.content_hash,
       coalesce(v.source_modified_at, v.published_at) AS source_date,
       r.representation_id, r.route, r.markdown_uri, r.markdown_hash,
       r.conversion_uri, r.manifest_hash,
       g.structure_generation_id, g.pageindex_uri,
       c.raw_uri, c.mime, m.family, m.title, m.file_name,
       s.summary AS root_summary, s.placement_path,
       EXISTS (
         SELECT 1 FROM document_crossrefs attachment
         JOIN document_reference_generations refs
           ON refs.deployment_id = attachment.deployment_id
          AND refs.version_id = attachment.from_version_id
          AND refs.generation_id = attachment.generation_id AND refs.status = 'active'
         JOIN documents parent
           ON parent.deployment_id = attachment.deployment_id
          AND parent.doc_id = attachment.from_doc_id AND parent.deleted_at IS NULL
          AND parent.current_version_id = attachment.from_version_id
         WHERE attachment.deployment_id = d.deployment_id
           AND attachment.to_doc_id = d.doc_id AND attachment.kind = 'attaches'
       ) AS is_attachment
FROM documents d
JOIN document_versions v
  ON v.deployment_id = d.deployment_id AND v.doc_id = d.doc_id
 AND v.version_id = d.current_version_id AND v.deleted_at IS NULL
JOIN document_representations r
  ON r.deployment_id = v.deployment_id AND r.version_id = v.version_id
 AND r.representation_id = v.current_representation_id
JOIN document_structure_generations g
  ON g.deployment_id = r.deployment_id AND g.representation_id = r.representation_id
 AND g.structure_generation_id = r.current_structure_generation_id
JOIN content_objects c
  ON c.deployment_id = v.deployment_id AND c.content_hash = v.content_hash
 AND c.purged_at IS NULL
JOIN document_metadata m
  ON m.deployment_id = v.deployment_id AND m.version_id = v.version_id
JOIN document_sections s
  ON s.deployment_id = g.deployment_id AND s.representation_id = g.representation_id
 AND s.structure_generation_id = g.structure_generation_id AND s.node_path = '0'
WHERE d.deployment_id = :deployment_id AND d.deleted_at IS NULL
"""
