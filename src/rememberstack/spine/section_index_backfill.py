"""Give pre-D140 sections their section keys and content hashes (D140 §4.2).

Sections written before D140 have NULL ``section_key``, ``own_content_hash``
and ``subtree_content_hash``. All three are pure functions of the
representation's stored ``blocks.json`` and the section's block range, so a
deterministic job recomputes them without a model and without reprocessing
the document. It updates only those three columns and only on rows whose hash
is still NULL, so running it again, or concurrently with the structure worker,
is harmless. Readers treat a section with NULL hashes as not indexed yet.
"""

from dataclasses import dataclass
import json
import logging
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import blocks_from_sidecar
from rememberstack.core import reindexed_sections
from rememberstack.model import Block
from rememberstack.model import ObjectKey
from rememberstack.model import SnappedSection
from rememberstack.model import StructureRouteTag
from rememberstack.ports.object_store import ObjectStorePort

logger = logging.getLogger(__name__)

SECTION_INDEX_BACKFILL_PAGE_SIZE: Final = 200
"""Structure generations read per page (a starting point, not a tuned value)."""

_HEADING_PARSER_ROUTES: Final = frozenset(
    {StructureRouteTag.PARSER.value, StructureRouteTag.PARSER_DEMOTED_CHECK.value}
)
"""Routes whose sections the deterministic heading parser produced; only
their headings can carry keys (model-anchored fallback sections never do)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionIndexBackfillResult:
    """What one backfill run changed."""

    generations_indexed: int
    sections_updated: int
    generations_skipped: int


class SectionIndexBackfill:
    """Backfill section keys and content hashes from stored block grids."""

    def __init__(self, *, engine: Engine, artifact_store: ObjectStorePort) -> None:
        """Bind the job to the deployment database and its artifact store."""
        self._engine = engine
        self._artifact_store = artifact_store

    def run(self, *, deployment_id: UUID) -> SectionIndexBackfillResult:
        """Index every structure generation that still has unindexed sections.

        Generations are walked in id order in bounded pages. A generation whose
        artifacts cannot be read, or whose stored ranges do not fit its grid, is
        logged and skipped (its sections stay ``not_indexed``); the walk moves
        past it, so a run always terminates.
        """
        indexed = 0
        updated = 0
        skipped = 0
        after: UUID | None = None
        while True:
            with self._engine.connect() as connection:
                generations = (
                    connection.execute(
                        _SELECT_PENDING_GENERATIONS,
                        {
                            "deployment_id": deployment_id,
                            "after": after,
                            "limit": SECTION_INDEX_BACKFILL_PAGE_SIZE,
                        },
                    )
                    .mappings()
                    .all()
                )
            if not generations:
                break
            for generation in generations:
                count = self._index_generation(generation=generation)
                if count is None:
                    skipped += 1
                else:
                    indexed += 1
                    updated += count
            after = generations[-1]["structure_generation_id"]
        return SectionIndexBackfillResult(
            generations_indexed=indexed,
            sections_updated=updated,
            generations_skipped=skipped,
        )

    def _index_generation(self, *, generation: RowMapping) -> int | None:
        """Update one generation's sections; ``None`` when it was skipped."""
        generation_id = generation["structure_generation_id"]
        try:
            blocks = self._blocks(
                blocks_uri=generation["blocks_uri"],
                markdown_uri=generation["markdown_uri"],
            )
        except Exception:  # noqa: BLE001 — one unreadable generation never stops the job
            logger.exception(
                "section index backfill: cannot read blocks for generation %s",
                generation_id,
            )
            return None
        with self._engine.begin() as connection:
            rows = (
                connection.execute(
                    _SELECT_GENERATION_SECTIONS,
                    {"structure_generation_id": generation_id},
                )
                .mappings()
                .all()
            )
            by_path = {row["node_path"]: row["section_id"] for row in rows}
            reindexed = reindexed_sections(
                sections=tuple(_stored_section(row=row) for row in rows),
                blocks=blocks,
                heading_keys=generation["route_tag"] in _HEADING_PARSER_ROUTES,
            )
            if reindexed is None:
                logger.warning(
                    "section index backfill: generation %s does not fit its"
                    " stored block grid; left unindexed",
                    generation_id,
                )
                return None
            result = connection.execute(
                _UPDATE_SECTION_INDEX,
                {
                    "section_ids": [by_path[item.node_path] for item in reindexed],
                    "section_keys": [item.section_key for item in reindexed],
                    "own_hashes": [item.own_content_hash for item in reindexed],
                    "subtree_hashes": [item.subtree_content_hash for item in reindexed],
                },
            )
            return result.rowcount

    def _blocks(self, *, blocks_uri: str, markdown_uri: str) -> tuple[Block, ...]:
        """The representation's block grid, re-derived only from a stale sidecar."""
        blocks_doc = json.loads(
            self._artifact_store.read_bytes(key=ObjectKey(blocks_uri))
        )
        markdown = ""
        if blocks_doc.get("blockizer_version") != BLOCKIZER_VERSION:
            markdown = self._artifact_store.read_bytes(
                key=ObjectKey(markdown_uri)
            ).decode("utf-8")
        return blocks_from_sidecar(blocks_doc=blocks_doc, document_md=markdown)


def _stored_section(*, row: RowMapping) -> SnappedSection:
    node_path = row["node_path"]
    return SnappedSection(
        node_path=node_path,
        parent_path=node_path.rsplit(".", 1)[0] if "." in node_path else None,
        title=row["title"] or "",
        role=row["role"],
        block_start=row["block_start"],
        block_end=row["block_end"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        summary=row["summary"],
        ordinal=row["ordinal"],
        heading_level=row["heading_level"],
        normalized_title=row["normalized_title"] or "",
    )


_SELECT_PENDING_GENERATIONS = text(
    """
    SELECT g.structure_generation_id, g.route_tag::text AS route_tag,
           r.blocks_uri, r.markdown_uri
    FROM document_structure_generations g
    JOIN document_representations r ON r.representation_id = g.representation_id
    WHERE g.deployment_id = :deployment_id
      AND (CAST(:after AS uuid) IS NULL
           OR g.structure_generation_id > CAST(:after AS uuid))
      AND EXISTS (
          SELECT 1 FROM document_sections s
          WHERE s.structure_generation_id = g.structure_generation_id
            AND s.own_content_hash IS NULL
      )
    ORDER BY g.structure_generation_id
    LIMIT :limit
    """
)

_SELECT_GENERATION_SECTIONS = text(
    """
    SELECT section_id, node_path, title, role::text AS role, block_start,
           block_end, char_start, char_end, summary, ordinal, heading_level,
           normalized_title
    FROM document_sections
    WHERE structure_generation_id = :structure_generation_id
    ORDER BY ordinal
    FOR UPDATE
    """
)

_UPDATE_SECTION_INDEX = text(
    """
    UPDATE document_sections s
    SET section_key = v.section_key,
        own_content_hash = v.own_content_hash,
        subtree_content_hash = v.subtree_content_hash
    FROM unnest(
        CAST(:section_ids AS uuid[]),
        CAST(:section_keys AS text[]),
        CAST(:own_hashes AS text[]),
        CAST(:subtree_hashes AS text[])
    ) AS v(section_id, section_key, own_content_hash, subtree_content_hash)
    WHERE s.section_id = v.section_id
      AND s.own_content_hash IS NULL
    """
)
