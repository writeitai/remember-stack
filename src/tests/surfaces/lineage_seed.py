"""Shared fixture helpers for memory_v1 surviving-lineage provenance.

`memory_v1` claim and entity views require a complete document chain, not just
leaf rows. Production extraction builds this chain; hand-seeded tests must
mirror it or the views correctly return nothing.

Two helpers cover the arms that fixtures need:

- :func:`seed_live_document_lineage` — document → content → version →
  representation → structure generation → section → chunk, with current
  pointers set so ``documents_live`` / ``document_versions_visible`` /
  ``claims_visible_history`` can see the rows.
- :func:`seed_entity_mention` — a mention on a real chunk plus its live
  resolution decision, the association production gives a real extracted
  Person/Organization/Concept. Do **not** use ``documents.document_entity_id``
  for those types: that column is the bridge for Document-typed registry
  entities, and seeding it here would leave mention-based provenance unguarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

_DEFAULT_AT = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LiveDocumentLineage:
    """Coordinates of one fully-wired live document lineage."""

    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    section_id: UUID
    generation_id: UUID
    chunk_ids: tuple[UUID, ...]

    @property
    def chunk_id(self) -> UUID:
        """The first (and often only) chunk on this lineage."""
        return self.chunk_ids[0]


def seed_live_document_lineage(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID | None = None,
    chunk_ids: tuple[UUID, ...] | None = None,
    content_hash: str | None = None,
    label: str = "lineage",
    title: str | None = None,
    source_ref: str | None = None,
    at: datetime | None = None,
    create_document: bool = True,
) -> LiveDocumentLineage:
    """Seed the full live lineage that ``memory_v1`` claim/entity views require.

    When ``create_document`` is False the document row must already exist; the
    rest of the chain is still created and the document's ``current_version_id``
    is pointed at the new version.
    """
    doc_id = doc_id or uuid4()
    version_id = uuid4()
    representation_id = uuid4()
    section_id = uuid4()
    generation_id = uuid4()
    chunks = chunk_ids if chunk_ids is not None else (uuid4(),)
    if not chunks:
        raise ValueError("chunk_ids must contain at least one chunk")
    content_hash = content_hash or f"{label}-{doc_id}"
    stamp = at or _DEFAULT_AT
    if create_document:
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :deployment, 'upload', :ref,"
                " :title)"
            ),
            {
                "doc": doc_id,
                "deployment": deployment_id,
                "ref": source_ref or f"{label}-{doc_id}",
                "title": title or f"Lineage {label}",
            },
        )
    connection.execute(
        text(
            "INSERT INTO content_objects (deployment_id, content_hash, mime,"
            " raw_uri) VALUES (:deployment, :hash, 'text/markdown', :uri)"
        ),
        {
            "deployment": deployment_id,
            "hash": content_hash,
            "uri": f"mem://raw/{content_hash}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
            " content_hash, version_no, status, source_modified_at) VALUES"
            " (:version, :deployment, :doc, :hash, 1, 'ready', :at)"
        ),
        {
            "version": version_id,
            "deployment": deployment_id,
            "doc": doc_id,
            "hash": content_hash,
            "at": stamp,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_representations (representation_id,"
            " deployment_id, version_id, route, markdown_uri, status) VALUES"
            " (:representation, :deployment, :version, 'digital', :uri, 'ready')"
        ),
        {
            "representation": representation_id,
            "deployment": deployment_id,
            "version": version_id,
            "uri": f"mem://artifacts/{content_hash}.md",
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_structure_generations"
            " (structure_generation_id, deployment_id, doc_id, version_id,"
            " representation_id, skeleton_version, skeleton_hash,"
            " skeleton_producer_family, roles_version, route_tag,"
            " candidate_skeleton_hash, stats_version, stats) VALUES"
            " (:generation, :deployment, :doc, :version, :representation,"
            " :label, 'skeleton', 'deterministic', :label, 'parser',"
            " 'candidate', :label, '{}'::jsonb)"
        ),
        {
            "generation": generation_id,
            "deployment": deployment_id,
            "doc": doc_id,
            "version": version_id,
            "representation": representation_id,
            "label": label[:64],
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
            " version_id, representation_id, node_path, block_start, block_end,"
            " role, char_start, char_end, ordinal, structure_generation_id)"
            " VALUES (:section, :deployment, :doc, :version, :representation,"
            " '0', 0, :block_end, 'body', 0, :char_end, 0, :generation)"
        ),
        {
            "section": section_id,
            "deployment": deployment_id,
            "doc": doc_id,
            "version": version_id,
            "representation": representation_id,
            "generation": generation_id,
            "block_end": max(len(chunks) - 1, 0),
            "char_end": len(chunks) * 100,
        },
    )
    connection.execute(
        text(
            "UPDATE document_representations SET"
            " current_structure_generation_id = :generation"
            " WHERE representation_id = :representation"
        ),
        {"generation": generation_id, "representation": representation_id},
    )
    connection.execute(
        text(
            "UPDATE document_versions SET current_representation_id ="
            " :representation WHERE version_id = :version"
        ),
        {"representation": representation_id, "version": version_id},
    )
    connection.execute(
        text("UPDATE documents SET current_version_id = :version WHERE doc_id = :doc"),
        {"version": version_id, "doc": doc_id},
    )
    for ordinal, chunk_id in enumerate(chunks):
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, section_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " context_prefix, created_at) VALUES (:chunk, :deployment, :doc,"
                " :version, :representation, :section, :ordinal, :ordinal,"
                " :ordinal, :content_hash, :input_hash, :start, :end, :prefix,"
                " :at)"
            ),
            {
                "chunk": chunk_id,
                "deployment": deployment_id,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "section": section_id,
                "ordinal": ordinal,
                "content_hash": f"{content_hash}-chunk-{ordinal}",
                "input_hash": f"{content_hash}-input-{ordinal}",
                "start": ordinal * 100,
                "end": ordinal * 100 + 90,
                "prefix": f"Context {ordinal}.",
                "at": stamp,
            },
        )
    return LiveDocumentLineage(
        doc_id=doc_id,
        version_id=version_id,
        representation_id=representation_id,
        section_id=section_id,
        generation_id=generation_id,
        chunk_ids=chunks,
    )


def seed_entity_mention(
    *,
    connection: Connection,
    deployment_id: UUID,
    entity_id: UUID,
    doc_id: UUID,
    chunk_id: UUID,
    surface_form: str,
    normalized_lemma: str | None = None,
    at: datetime | None = None,
    resolver_version: str = "test",
    claim_id: UUID | None = None,
) -> UUID:
    """Give an entity ``entities_current`` membership via a live mention.

    This is the production association for Person/Organization/Concept entities:
    a mention on a real chunk of a live lineage, plus a non-superseded resolution
    decision. Callers must pass a chunk that already sits on a complete live
    lineage (see :func:`seed_live_document_lineage`).
    """
    mention_id = uuid4()
    stamp = at or _DEFAULT_AT
    lemma = normalized_lemma if normalized_lemma is not None else surface_form.lower()
    connection.execute(
        text(
            "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
            " normalized_lemma, chunk_id, claim_id, doc_id, created_at) VALUES"
            " (:mention, :deployment, :surface, :lemma, :chunk, :claim, :doc,"
            " :at)"
        ),
        {
            "mention": mention_id,
            "deployment": deployment_id,
            "surface": surface_form,
            "lemma": lemma,
            "chunk": chunk_id,
            "claim": claim_id,
            "doc": doc_id,
            "at": stamp,
        },
    )
    connection.execute(
        text(
            "INSERT INTO resolution_decisions (decision_id, deployment_id,"
            " mention_id, entity_id, method, confidence, resolver_version,"
            " decided_at) VALUES (:decision, :deployment, :mention, :entity,"
            " 'T0', 1.0, :version, :at)"
        ),
        {
            "decision": uuid4(),
            "deployment": deployment_id,
            "mention": mention_id,
            "entity": entity_id,
            "version": resolver_version,
            "at": stamp,
        },
    )
    return mention_id
