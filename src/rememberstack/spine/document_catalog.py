"""The E0 document catalog: lineage, representation, and D79 generations.

Spine-owned SQL for the D36 sub-worker chain over the D55 lineage model:
`record_upload` lands content + lineage + version rows and enqueues convert
atomically; `record_representation` lands one immutable conversion output
(D65); immutable section generations carry an explicit current pointer and
skeleton checks append independently under D52.
"""

from collections.abc import Sequence
from decimal import Decimal
import json
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.model import ConvertSource
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import IngestedVersion
from rememberstack.model import PersistedSectionTree
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RepresentationNotFoundError
from rememberstack.model import RepresentationRecord
from rememberstack.model import SectionTreeRecord
from rememberstack.model import SkeletonCheckRecord
from rememberstack.model import SkeletonStats
from rememberstack.model import SnappedSection
from rememberstack.model import StructureRouteTag
from rememberstack.model import StructureSource
from rememberstack.model import SyntheticRootRecord
from rememberstack.model import UploadRecord
from rememberstack.spine.work_ledger import enqueue_on


class DocumentCatalog:
    """E0 row writes and stage loads over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def record_upload(
        self,
        *,
        record: UploadRecord,
        convert_component_version: str,
        lane: ProcessingLane = ProcessingLane.STEADY,
    ) -> IngestedVersion:
        """Land one upload's rows and enqueue its convert work in one transaction.

        Bytes identical to the lineage's LATEST version are the D55
        content-hash no-op: that version is returned with ``created=False``,
        no new work is created (the idempotent enqueue still runs, healing a
        crash that committed rows without their work row), and the version's
        source cursor advances to the newly observed revision — revision
        churn without byte changes must not refetch forever. Semantic snapshot
        metadata, including ``source_modified_at``, remains immutable because it
        already fed extraction. Bytes matching only an OLDER version (content
        reverted A→B→A) are a new observation and become a new version: the
        lineage moves forward, never silently back to a stale current pointer.
        """
        with self._engine.begin() as connection:
            doc_id = _lineage_locked(connection=connection, record=record)
            connection.execute(
                _INSERT_CONTENT_OBJECT,
                {
                    "deployment_id": record.deployment_id,
                    "content_hash": record.content_hash,
                    "mime": record.mime,
                    "byte_size": record.byte_size,
                    "raw_uri": record.raw_uri,
                },
            )
            latest = (
                connection.execute(
                    _SELECT_LATEST_VERSION,
                    {"deployment_id": record.deployment_id, "doc_id": doc_id},
                )
                .mappings()
                .one_or_none()
            )
            created = latest is None or latest["content_hash"] != record.content_hash
            version_id = uuid4() if created or latest is None else latest["version_id"]
            if created:
                connection.execute(
                    _INSERT_VERSION,
                    {
                        "version_id": version_id,
                        "deployment_id": record.deployment_id,
                        "doc_id": doc_id,
                        "content_hash": record.content_hash,
                        "source_modified_at": record.source_modified_at,
                        "source_version_ref": record.source_version_ref,
                        "sync_cycle_id": record.sync_cycle_id,
                    },
                )
            elif record.source_version_ref is not None:
                connection.execute(
                    _ADVANCE_VERSION_CURSOR,
                    {
                        "version_id": version_id,
                        "source_version_ref": record.source_version_ref,
                    },
                )
            enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=record.deployment_id,
                    # the idempotency key names the VERSION (D12/D55): a
                    # lineage's second version must never collide with the
                    # first version's completed work row
                    target_kind=ProcessingTarget.DOCUMENT_VERSION,
                    target_id=version_id,
                    stage=PipelineStage.CONVERT,
                    component_version=convert_component_version,
                    content_hash=record.content_hash,
                    lane=lane,
                    payload={"version_id": str(version_id)},
                ),
            )
            return IngestedVersion(
                deployment_id=record.deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                content_hash=record.content_hash,
                created=created,
            )

    def convert_source(self, *, version_id: UUID) -> ConvertSource:
        """Load what the convert stage needs about one document version."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_CONVERT_SOURCE, {"version_id": version_id})
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DocumentVersionNotFoundError(
                f"document version {version_id} does not exist"
            )
        return ConvertSource.model_validate(dict(row))

    def existing_representation(
        self,
        *,
        version_id: UUID,
        route: str,
        converter_version: str,
        blockizer_version: str,
    ) -> UUID | None:
        """Find a prior conversion of this version by the same toolchain (D65/D7).

        A retried convert attempt replays the stored representation instead of
        re-calling the converter and minting a second immutable reading.
        """
        with self._engine.connect() as connection:
            return connection.execute(
                _SELECT_EXISTING_REPRESENTATION,
                {
                    "version_id": version_id,
                    "route": route,
                    "converter_version": converter_version,
                    "blockizer_version": blockizer_version,
                },
            ).scalar_one_or_none()

    def mark_version_failed(self, *, version_id: UUID, error: str) -> None:
        """Record a permanent conversion failure on the version's own status.

        A dead-lettered convert must not leave the document looking in-flight
        forever; a version that already reached ``ready`` is never demoted,
        and the first recorded cause is never overwritten by a later, more
        generic finalizer message.
        """
        with self._engine.begin() as connection:
            connection.execute(
                _MARK_VERSION_FAILED, {"version_id": version_id, "error": error}
            )

    def record_representation(self, *, record: RepresentationRecord) -> None:
        """Insert one immutable conversion output and advance the version (D65).

        The representation lands in ``structuring`` status; the structure stage
        completes it. The version's live-reading pointer is NOT set here — it
        swaps only on chain completion (`record_synthetic_root`), the D54 rule.
        """
        with self._engine.begin() as connection:
            connection.execute(_INSERT_REPRESENTATION, record.model_dump(mode="json"))
            connection.execute(
                _MARK_VERSION_STRUCTURING,
                {
                    "version_id": record.version_id,
                    "deployment_id": record.deployment_id,
                },
            )

    def structure_source(self, *, representation_id: UUID) -> StructureSource:
        """Load what the structure stage needs about one representation."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_STRUCTURE_SOURCE, {"representation_id": representation_id}
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise RepresentationNotFoundError(
                f"document representation {representation_id} does not exist"
            )
        return StructureSource.model_validate(dict(row))

    def current_section_tree(
        self, *, representation_id: UUID
    ) -> PersistedSectionTree | None:
        """Load the representation's current immutable structure generation."""
        with self._engine.connect() as connection:
            generation = (
                connection.execute(
                    _SELECT_CURRENT_STRUCTURE_GENERATION,
                    {"representation_id": representation_id},
                )
                .mappings()
                .one_or_none()
            )
            if generation is None:
                return None
            persisted = (
                connection.execute(
                    _SELECT_SECTION_TREE,
                    {"structure_generation_id": generation["structure_generation_id"]},
                )
                .mappings()
                .all()
            )
        if not persisted:
            raise RuntimeError("current structure generation has no root section")
        return _persisted_tree(generation=generation, sections=persisted)

    def summary_cache_sidecars(self, *, doc_id: UUID) -> tuple[str, ...]:
        """All prior sidecars that may contain keyed summary cache entries."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_SUMMARY_CACHE_SIDECARS, {"doc_id": doc_id}
            ).scalars()
            return tuple(str(uri) for uri in rows)

    def record_synthetic_root(self, *, record: SyntheticRootRecord) -> None:
        """Compatibility helper for callers that explicitly request a root.

        The D79 worker uses ``record_section_tree`` directly. This helper wraps
        older internal callers in one deterministic legacy generation.
        """
        generation_id = uuid5(
            NAMESPACE_URL,
            "rememberstack:legacy-synthetic:"
            f"{record.representation_id}:{record.structurer_version}",
        )
        stats = SkeletonStats(
            stats_version="legacy-synthetic",
            section_count=0,
            duplicate_title_ratio=None,
            max_title_multiplicity=None,
            sibling_duplicate_ratio=None,
            level_jump_count=None,
            numbering_coverage=None,
            numbering_inversions=None,
            numbering_scheme_switches=None,
            tiny_section_ratio=None,
            zero_direct_body_ratio=None,
            oversized_leaf_ratio=None,
            heading_density=None,
            title_length_p50=None,
            title_length_p95=None,
            long_title_ratio=None,
            low_letter_ratio=None,
            empty_title_ratio=None,
            max_sibling_fanout=None,
        )
        digest = str(generation_id).replace("-", "")
        self.record_section_tree(
            record=SectionTreeRecord(
                deployment_id=record.deployment_id,
                doc_id=record.doc_id,
                version_id=record.version_id,
                representation_id=record.representation_id,
                structure_generation_id=generation_id,
                sections=(
                    SnappedSection(
                        node_path="0",
                        parent_path=None,
                        title=record.title or "",
                        role="body",
                        block_start=0,
                        block_end=record.block_count - 1,
                        char_start=0,
                        char_end=record.markdown_chars,
                        summary="",
                        ordinal=0,
                        normalized_title=(record.title or "").casefold(),
                    ),
                ),
                placement_path=None,
                structurer_name="synthetic_root",
                structurer_version=record.structurer_version,
                skeleton_version=record.structurer_version,
                skeleton_hash=digest,
                skeleton_producer_family="N/A",
                skeleton_check_version=None,
                roles_version=record.structurer_version,
                selecting_check_id=None,
                route_tag=StructureRouteTag.LEGACY,
                candidate_skeleton_hash=digest,
                stats_version=stats.stats_version,
                stats=stats,
                pageindex_uri=f"legacy://pageindex/{generation_id}.json",
            )
        )

    def record_skeleton_check(self, *, record: SkeletonCheckRecord) -> None:
        """Append one checker record without ever storing completion text."""
        payload = record.model_dump(mode="json")
        payload["stats"] = json.dumps(
            payload["stats"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        failure = payload["provider_failure"]
        payload["provider_failure"] = (
            json.dumps(
                failure, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if failure is not None
            else None
        )
        with self._engine.begin() as connection:
            connection.execute(_INSERT_SKELETON_CHECK, payload)

    def skeleton_checks(
        self, *, representation_id: UUID
    ) -> tuple[SkeletonCheckRecord, ...]:
        """Read a representation's append-only checks in insertion order."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_SKELETON_CHECKS, {"representation_id": representation_id}
                )
                .mappings()
                .all()
            )
        return tuple(
            SkeletonCheckRecord.model_validate(
                {
                    **dict(row),
                    "stats": dict(row["stats"]),
                    "cost_usd": (
                        Decimal(row["cost_usd"])
                        if row["cost_usd"] is not None
                        else None
                    ),
                }
            )
            for row in rows
        )

    def record_section_tree(self, *, record: SectionTreeRecord) -> PersistedSectionTree:
        """Complete the E0 chain for one representation in one transaction (D39/D54).

        Inserts one immutable generation and its rows (root first, parents
        resolved by path), marks the representation ready, swaps the version's live-reading
        pointer, and moves the lineage's current-version pointer — so
        currency flips only when the chain is whole. The walking skeleton's
        chain ends at structure; when the E1/E2 stages land, this flip moves
        with the chain's end (D54's rule is "after conversion→E1→E2
        completes"). Every statement is idempotent for a retried attempt
        (a generation-id conflict skips the entire proposed tree and keeps the
        first-written generation), the pointer swap never overwrites a different
        live representation, and the currency pointer only moves FORWARD by
        version number — a delayed older version completing after a newer
        one must not drag the lineage back to stale content.

        Returns what is actually PERSISTED — on a retry that lost to an
        earlier attempt, that is the earlier attempt's tree, and derived
        artifacts (the sidecar) must be built from it, not from the input.
        """
        with self._engine.begin() as connection:
            generation_payload = record.model_dump(mode="json")
            generation_payload["stats"] = json.dumps(
                generation_payload["stats"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            inserted_generation = connection.execute(
                _INSERT_STRUCTURE_GENERATION, generation_payload
            ).scalar_one_or_none()
            ids_by_path: dict[str, UUID] = {}
            if inserted_generation is not None:
                for section in record.sections:
                    parent_id = (
                        ids_by_path[section.parent_path]
                        if section.parent_path is not None
                        else None
                    )
                    section_id = connection.execute(
                        _INSERT_SECTION,
                        {
                            "section_id": uuid4(),
                            "deployment_id": record.deployment_id,
                            "doc_id": record.doc_id,
                            "version_id": record.version_id,
                            "representation_id": record.representation_id,
                            "structure_generation_id": record.structure_generation_id,
                            "parent_section_id": parent_id,
                            "node_path": section.node_path,
                            "block_start": section.block_start,
                            "block_end": section.block_end,
                            "title": section.title or None,
                            "role": section.role,
                            "char_start": section.char_start,
                            "char_end": section.char_end,
                            "ordinal": section.ordinal,
                            "heading_level": section.heading_level,
                            "normalized_title": section.normalized_title,
                            "summary": section.summary or None,
                            "placement_path": (
                                record.placement_path
                                if section.parent_path is None
                                else None
                            ),
                            "structurer_version": record.structurer_version,
                        },
                    ).scalar_one()
                    ids_by_path[section.node_path] = section_id
            persisted = (
                connection.execute(
                    _SELECT_SECTION_TREE,
                    {"structure_generation_id": record.structure_generation_id},
                )
                .mappings()
                .all()
            )
            if not persisted:
                raise RuntimeError(
                    "structure generation exists without its root section"
                )
            generation = (
                connection.execute(
                    _SELECT_STRUCTURE_GENERATION,
                    {"structure_generation_id": record.structure_generation_id},
                )
                .mappings()
                .one()
            )
            if record.make_current:
                connection.execute(
                    _MARK_REPRESENTATION_READY,
                    {
                        "representation_id": record.representation_id,
                        "structure_generation_id": record.structure_generation_id,
                        "pageindex_uri": generation["pageindex_uri"],
                        "structurer_name": generation["route_tag"],
                        "structurer_version": persisted[0]["structurer_version"],
                    },
                )
                connection.execute(
                    _MARK_VERSION_READY,
                    {
                        "version_id": record.version_id,
                        "representation_id": record.representation_id,
                    },
                )
                connection.execute(
                    _MARK_LINEAGE_CURRENT,
                    {"doc_id": record.doc_id, "version_id": record.version_id},
                )
                connection.execute(  # the lineage pointer moved: older versions
                    _SUPERSEDE_PRIOR_VERSIONS,  # are superseded as of now (D55)
                    {"doc_id": record.doc_id, "version_id": record.version_id},
                )
        return _persisted_tree(generation=generation, sections=persisted)


def _lineage_locked(*, connection: Connection, record: UploadRecord) -> UUID:
    """Create or lock the upload's lineage row; returns its doc_id.

    The insert-or-lock serializes concurrent ingests of one lineage so the
    version-number assignment below it is race-free. An ingest is an
    observation that the source EXISTS, so a tombstoned lineage re-observed
    (delete-and-recreate, D55's self-healing case) is resurrected here —
    otherwise the recreated file would attach versions to a dead lineage
    that never resurfaces and gets refetched on every poll.
    """
    inserted = connection.execute(
        _INSERT_DOCUMENT,
        {
            "doc_id": record.doc_id,
            "deployment_id": record.deployment_id,
            "source_kind": record.source_kind,
            "source_ref": record.source_ref,
            "source_uri": record.source_uri,
            "title": record.title,
            "versioning_mode": record.versioning_mode,
        },
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    doc_id = connection.execute(
        _SELECT_DOCUMENT_LOCKED,
        {
            "deployment_id": record.deployment_id,
            "source_kind": record.source_kind,
            "source_ref": record.source_ref,
        },
    ).scalar_one()
    connection.execute(_RESURRECT_LINEAGE, {"doc_id": doc_id})
    return doc_id


_INSERT_DOCUMENT = text(
    """
    INSERT INTO documents (
        doc_id, deployment_id, source_kind, source_ref, source_uri, title,
        versioning_mode
    ) VALUES (
        :doc_id, :deployment_id, :source_kind, :source_ref, :source_uri, :title,
        CAST(:versioning_mode AS versioning_mode)
    )
    ON CONFLICT (deployment_id, source_kind, source_ref) DO NOTHING
    RETURNING doc_id
    """
)

_SELECT_DOCUMENT_LOCKED = text(
    """
    SELECT doc_id FROM documents
    WHERE deployment_id = :deployment_id
      AND source_kind = :source_kind
      AND source_ref = :source_ref
    FOR UPDATE
    """
)

_INSERT_CONTENT_OBJECT = text(
    """
    INSERT INTO content_objects (
        deployment_id, content_hash, mime, byte_size, raw_uri
    ) VALUES (
        :deployment_id, :content_hash, :mime, :byte_size, :raw_uri
    )
    ON CONFLICT (deployment_id, content_hash) DO NOTHING
    """
)

_SELECT_LATEST_VERSION = text(
    """
    SELECT version_id, content_hash FROM document_versions
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ORDER BY version_no DESC
    LIMIT 1
    """
)

_ADVANCE_VERSION_CURSOR = text(
    """
    UPDATE document_versions
    SET source_version_ref = :source_version_ref
    WHERE version_id = :version_id
    """
)

_RESURRECT_LINEAGE = text(
    """
    UPDATE documents
    SET deleted_at = NULL, deleted_sync_cycle_id = NULL
    WHERE doc_id = :doc_id AND deleted_at IS NOT NULL
    """
)

_INSERT_VERSION = text(
    """
    INSERT INTO document_versions (
        version_id, deployment_id, doc_id, content_hash, version_no, status,
        source_modified_at, source_version_ref, sync_cycle_id
    ) VALUES (
        :version_id, :deployment_id, :doc_id, :content_hash,
        (SELECT coalesce(max(version_no), 0) + 1 FROM document_versions
         WHERE deployment_id = :deployment_id AND doc_id = :doc_id),
        'converting',
        :source_modified_at, :source_version_ref, :sync_cycle_id
    )
    """
)

_SELECT_CONVERT_SOURCE = text(
    """
    SELECT v.deployment_id, v.doc_id, v.version_id, v.content_hash,
           c.mime, c.raw_uri, d.title
    FROM document_versions v
    JOIN content_objects c
      ON c.deployment_id = v.deployment_id AND c.content_hash = v.content_hash
    JOIN documents d ON d.doc_id = v.doc_id
    WHERE v.version_id = :version_id
    """
)

_INSERT_REPRESENTATION = text(
    """
    INSERT INTO document_representations (
        representation_id, deployment_id, version_id, route,
        converter_name, converter_version, blockizer_version,
        markdown_uri, blocks_uri, conversion_uri, meta_uri,
        markdown_hash, manifest_hash, status
    ) VALUES (
        :representation_id, :deployment_id, :version_id, :route,
        :converter_name, :converter_version, :blockizer_version,
        :markdown_uri, :blocks_uri, :conversion_uri, :meta_uri,
        :markdown_hash, :manifest_hash, 'structuring'
    )
    """
)

_MARK_VERSION_STRUCTURING = text(
    """
    UPDATE document_versions SET status = 'structuring'
    WHERE version_id = :version_id AND deployment_id = :deployment_id
    """
)

_SELECT_STRUCTURE_SOURCE = text(
    """
    SELECT r.deployment_id, v.doc_id, r.version_id, r.representation_id,
           r.blocks_uri, r.markdown_uri, d.title, d.source_kind
    FROM document_representations r
    JOIN document_versions v ON v.version_id = r.version_id
    JOIN documents d ON d.doc_id = v.doc_id
    WHERE r.representation_id = :representation_id
    """
)

_INSERT_SKELETON_CHECK = text(
    """
    INSERT INTO document_skeleton_checks (
        check_id, processing_id, deployment_id, doc_id, version_id,
        representation_id, candidate_skeleton_hash, stats_version, stats,
        sampled_input_hash, check_outcome, checker_component_version,
        checker_model, checker_model_hash, checker_prompt_hash,
        checker_schema_hash, provider_failure, tokens_in, tokens_out,
        cost_usd, latency_ms
    ) VALUES (
        :check_id, :processing_id, :deployment_id, :doc_id, :version_id,
        :representation_id, :candidate_skeleton_hash, :stats_version,
        CAST(:stats AS jsonb), :sampled_input_hash,
        CAST(:check_outcome AS skeleton_check_outcome),
        :checker_component_version, :checker_model, :checker_model_hash,
        :checker_prompt_hash, :checker_schema_hash,
        CAST(:provider_failure AS jsonb), :tokens_in, :tokens_out,
        :cost_usd, :latency_ms
    )
    """
)

_SELECT_SKELETON_CHECKS = text(
    """
    SELECT check_id, processing_id, deployment_id, doc_id, version_id,
           representation_id, candidate_skeleton_hash, stats_version, stats,
           sampled_input_hash, check_outcome::text AS check_outcome,
           checker_component_version, checker_model, checker_model_hash,
           checker_prompt_hash, checker_schema_hash, provider_failure,
           tokens_in, tokens_out, cost_usd, latency_ms
    FROM document_skeleton_checks
    WHERE representation_id = :representation_id
    ORDER BY checked_at, check_id
    """
)

_INSERT_STRUCTURE_GENERATION = text(
    """
    INSERT INTO document_structure_generations (
        structure_generation_id, deployment_id, doc_id, version_id,
        representation_id, skeleton_version, skeleton_hash,
        skeleton_producer_family, skeleton_check_version, roles_version,
        summary_version, placement_version, selecting_check_id, route_tag,
        candidate_skeleton_hash, stats_version, stats, pageindex_uri
    ) VALUES (
        :structure_generation_id, :deployment_id, :doc_id, :version_id,
        :representation_id, :skeleton_version, :skeleton_hash,
        :skeleton_producer_family, :skeleton_check_version, :roles_version,
        :summary_version, :placement_version, :selecting_check_id,
        CAST(:route_tag AS structure_route_tag), :candidate_skeleton_hash,
        :stats_version, CAST(:stats AS jsonb), :pageindex_uri
    )
    ON CONFLICT (structure_generation_id) DO NOTHING
    RETURNING structure_generation_id
    """
)

_INSERT_SECTION = text(
    """
    INSERT INTO document_sections (
        section_id, deployment_id, doc_id, version_id, representation_id,
        structure_generation_id, parent_section_id, node_path, block_start, block_end,
        title, role, char_start, char_end, ordinal,
        heading_level, normalized_title, summary, placement_path, structurer_version
    ) VALUES (
        :section_id, :deployment_id, :doc_id, :version_id, :representation_id,
        :structure_generation_id, :parent_section_id, :node_path, :block_start, :block_end,
        :title, CAST(:role AS section_role), :char_start, :char_end, :ordinal,
        :heading_level, :normalized_title, :summary, :placement_path, :structurer_version
    )
    ON CONFLICT (structure_generation_id, node_path) DO NOTHING
    RETURNING section_id
    """
)

_SELECT_STRUCTURE_GENERATION = text(
    """
    SELECT structure_generation_id, skeleton_version, skeleton_hash,
           skeleton_producer_family, skeleton_check_version, roles_version,
           summary_version, placement_version, selecting_check_id,
           route_tag::text AS route_tag, candidate_skeleton_hash,
           stats_version, stats, pageindex_uri
    FROM document_structure_generations
    WHERE structure_generation_id = :structure_generation_id
    """
)

_SELECT_CURRENT_STRUCTURE_GENERATION = text(
    """
    SELECT g.structure_generation_id, g.skeleton_version, g.skeleton_hash,
           g.skeleton_producer_family, g.skeleton_check_version, g.roles_version,
           g.summary_version, g.placement_version, g.selecting_check_id,
           g.route_tag::text AS route_tag, g.candidate_skeleton_hash,
           g.stats_version, g.stats, g.pageindex_uri
    FROM document_representations r
    JOIN document_structure_generations g
      ON g.representation_id = r.representation_id
     AND g.structure_generation_id = r.current_structure_generation_id
    WHERE r.representation_id = :representation_id
    """
)

_SELECT_SUMMARY_CACHE_SIDECARS = text(
    """
    SELECT pageindex_uri
    FROM document_structure_generations
    WHERE doc_id = :doc_id
      AND pageindex_uri IS NOT NULL
    ORDER BY created_at DESC, structure_generation_id
    """
)

_SELECT_SECTION_TREE = text(
    """
    SELECT node_path, title, role::text AS role, block_start, block_end,
           char_start, char_end, summary, ordinal, placement_path,
           structurer_version, heading_level, normalized_title
    FROM document_sections
    WHERE structure_generation_id = :structure_generation_id
    ORDER BY ordinal
    """
)


def _persisted_tree(
    *, generation: RowMapping, sections: Sequence[RowMapping]
) -> PersistedSectionTree:
    """Materialize one immutable generation from SQL mapping rows."""
    generation_row = generation
    section_rows = sections
    return PersistedSectionTree(
        sections=tuple(
            SnappedSection(
                node_path=row["node_path"],
                parent_path=(
                    row["node_path"].rsplit(".", 1)[0]
                    if "." in row["node_path"]
                    else None
                ),
                title=row["title"] or "",
                role=row["role"],
                block_start=row["block_start"],
                block_end=row["block_end"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                summary=row["summary"],
                ordinal=row["ordinal"],
                heading_level=row["heading_level"],
                normalized_title=row["normalized_title"],
            )
            for row in section_rows
        ),
        placement_path=section_rows[0]["placement_path"],
        structurer_version=section_rows[0]["structurer_version"] or "",
        structure_generation_id=generation_row["structure_generation_id"],
        pageindex_uri=generation_row["pageindex_uri"] or "",
        skeleton_version=generation_row["skeleton_version"],
        skeleton_hash=generation_row["skeleton_hash"],
        skeleton_producer_family=generation_row["skeleton_producer_family"],
        skeleton_check_version=generation_row["skeleton_check_version"],
        roles_version=generation_row["roles_version"],
        summary_version=generation_row["summary_version"],
        placement_version=generation_row["placement_version"],
        selecting_check_id=generation_row["selecting_check_id"],
        route_tag=generation_row["route_tag"],
        candidate_skeleton_hash=generation_row["candidate_skeleton_hash"],
        stats_version=generation_row["stats_version"],
        stats=SkeletonStats.model_validate(generation_row["stats"]),
    )


_MARK_REPRESENTATION_READY = text(
    """
    UPDATE document_representations
    SET structurer_name = :structurer_name,
        structurer_version = :structurer_version,
        section_index_version = :structurer_version,
        current_structure_generation_id = :structure_generation_id,
        pageindex_uri = :pageindex_uri,
        status = 'ready'
    WHERE representation_id = :representation_id
      AND status IN ('structuring', 'ready')
    """
)

_MARK_VERSION_READY = text(
    """
    UPDATE document_versions
    SET current_representation_id = :representation_id, status = 'ready'
    WHERE version_id = :version_id
      AND (current_representation_id IS NULL
           OR current_representation_id = :representation_id)
    """
)

_MARK_VERSION_FAILED = text(
    """
    UPDATE document_versions
    SET status = 'failed', error = COALESCE(error, :error)
    WHERE version_id = :version_id AND status <> 'ready'
    """
)

_SELECT_EXISTING_REPRESENTATION = text(
    """
    SELECT representation_id FROM document_representations
    WHERE version_id = :version_id
      AND route = :route
      AND converter_version = :converter_version
      AND blockizer_version = :blockizer_version
      AND status IN ('structuring', 'ready')
    ORDER BY created_at
    LIMIT 1
    """
)

_MARK_LINEAGE_CURRENT = text(
    """
    UPDATE documents d
    SET current_version_id = :version_id, last_observed_at = now()
    WHERE d.doc_id = :doc_id
      AND (d.current_version_id IS NULL
           OR (SELECT version_no FROM document_versions
               WHERE version_id = d.current_version_id)
              < (SELECT version_no FROM document_versions
                 WHERE version_id = :version_id))
    """
)

_SUPERSEDE_PRIOR_VERSIONS = text(
    """
    UPDATE document_versions SET superseded_at = now()
    WHERE doc_id = :doc_id
      AND version_id <> :version_id
      AND superseded_at IS NULL
      AND version_no < (SELECT version_no FROM document_versions
                        WHERE version_id = :version_id)
    """
)
