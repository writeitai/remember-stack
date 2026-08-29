"""The E0 chain (D36): upload → ingest → convert → structure.

The upload connector performs ingest synchronously (bytes to the raw store,
rows + convert work atomically through the catalog); convert and structure are
queued stage handlers. Artifacts land ID-addressed in the artifacts store
(`<doc_id>/<content_hash>/<representation_id>/…`, D37/D65); Postgres carries
only the index.

The D79 structure stage parses canonical heading blocks, checks eligible
skeletons with a bounded judge, uses an anchor-only LLM fallback for deficient
trees, assigns title-only roles, and appends immutable generations. A document
still never fails structuring.
"""

from collections.abc import Iterable
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
from typing import Final
from typing import Protocol
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from pydantic import Field
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import analyze_skeleton
from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import blocks_from_sidecar
from rememberstack.core import ConversionRouter
from rememberstack.core import deterministic_section_role
from rememberstack.core import LONG_TITLE
from rememberstack.core import MAX_FALLBACK_DEPTH
from rememberstack.core import MIN_CHECK_SECTIONS
from rememberstack.core import parse_heading_skeleton
from rememberstack.core import resolve_fallback_skeleton
from rememberstack.core import SECTION_ROLES
from rememberstack.core import skeleton_hash
from rememberstack.core import SKELETON_PARSER_VERSION
from rememberstack.core import SKELETON_STATS_VERSION
from rememberstack.core import SkeletonAnalysis
from rememberstack.core import storage_class_for
from rememberstack.model import Block
from rememberstack.model import ClaimedWork
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import DocumentUpload
from rememberstack.model import EnqueueWork
from rememberstack.model import FallbackStructureResponse
from rememberstack.model import IngestedVersion
from rememberstack.model import ModelRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import RepresentationRecord
from rememberstack.model import RoleClassificationResponse
from rememberstack.model import SectionTreeRecord
from rememberstack.model import SkeletonCheckOutcome
from rememberstack.model import SkeletonCheckRecord
from rememberstack.model import SkeletonCheckResponse
from rememberstack.model import SnappedSection
from rememberstack.model import StructureRouteTag
from rememberstack.model import StructureSource
from rememberstack.model import UnroutableMimeError
from rememberstack.model import UploadRecord
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e0_summary import SectionSummarizer
from rememberstack.workers.e0_summary import SummarySettings
from rememberstack.workers.e1 import E1_CHUNK_VERSION

E0_CONVERT_VERSION: Final = "e0-convert-2026.08"
"""The convert sub-worker's component version (D12 idempotency key member)."""

E0_STRUCTURE_VERSION: Final = "e0-structure-2026.07f:d79-wave2"
"""The aggregate Wave-2 generation identity.

Maps old ``e0-structure-2026.07c:temp0-1`` (one-shot offsets/tree/roles/
summaries/placement) to the D79 split: deterministic/anchor skeleton, sanity
check, role pass, bottom-up summaries, and root-reduction placement.
The ``07f`` seed is input/seat identity only; provider output never mints.
"""

E0_SKELETON_VERSION: Final = (
    f"e0-skeleton-2026.07a:d79:{SKELETON_PARSER_VERSION}"
    f":anchor-v1-depth{MAX_FALLBACK_DEPTH}"
)
"""Skeleton contract: canonical heading stack plus exact-anchor fallback."""

SKELETON_CHECK_PROMPT_CEILING: Final = 16_000
"""Hard total checker prompt ceiling, pinned by the checker version."""

E0_SKELETON_CHECK_VERSION: Final = (
    f"e0-skeleton-check-2026.07a:{SKELETON_STATS_VERSION}"
    f":sample-v2:enum-v1:ceiling{SKELETON_CHECK_PROMPT_CEILING}"
)
"""Checker prompt/sampling/schema generation. sample-v2: the greedy fit went
arithmetic (conservative digit/newline padding), so boundary selections can
differ from the per-trial re-render greedy it replaced."""

ROLE_PROMPT_CEILING: Final = 12_000
"""Hard total title-only role prompt ceiling, pinned by the role version."""

E0_ROLE_VERSION: Final = (
    f"e0-role-2026.07a:title-rules-v1:classifier-v1:ceiling{ROLE_PROMPT_CEILING}"
)
"""Deterministic normalized-title rules plus bounded title-only classifier."""

UPLOAD_SOURCE_KIND: Final = "upload"
"""The one-shot upload connector's source kind (D55 lineage identity)."""


class IngestAdmission(Protocol):
    """The one D74 check required before an ingest writes any bytes."""

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Raise when admission is closed or the identity was forgotten."""
        ...


class UploadIngestor:
    """The upload connector's ingest: bytes to the raw store, rows + work to the spine.

    A one-shot upload has no connector-native identity, so its lineage IS its
    content: `source_ref = content_hash` and a content-derived `doc_id`, which
    makes re-ingesting identical bytes a deterministic no-op (D55) and lets
    the raw object be written before any row exists.
    """

    def __init__(
        self,
        *,
        catalog: DocumentCatalog,
        raw_store: ObjectStorePort,
        admission: IngestAdmission,
    ) -> None:
        """Bind the connector to the catalog and the deployment's raw bucket."""
        self._catalog = catalog
        self._raw_store = raw_store
        self._admission = admission

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        lane: ProcessingLane = ProcessingLane.STEADY,
    ) -> IngestedVersion:
        """Ingest one uploaded file and enqueue its convert work."""
        content_hash = hashlib.sha256(upload.content).hexdigest()
        self._guard_ingest(
            deployment_id=deployment_id,
            source_kind=UPLOAD_SOURCE_KIND,
            source_ref=content_hash,
            content_hash=content_hash,
        )
        doc_id = uuid5(
            NAMESPACE_URL, f"rememberstack:upload:{deployment_id}:{content_hash}"
        )
        suffix = PurePosixPath(upload.filename).suffix
        raw_uri = f"{doc_id}/{content_hash}/original{suffix}"
        try:
            self._raw_store.write_bytes(
                key=ObjectKey(raw_uri),
                content=upload.content,
                # D51: media a harness reads stays hot; text originals
                # kept only for audit go cold — routed at the write
                storage_class=storage_class_for(mime=upload.mime),
            )
        except ObjectAlreadyExistsError:
            pass  # identical bytes already landed — ingest retries are no-ops
        return self._catalog.record_upload(
            record=UploadRecord(
                deployment_id=deployment_id,
                doc_id=doc_id,
                source_kind=UPLOAD_SOURCE_KIND,
                source_ref=content_hash,
                source_uri=None,
                title=upload.title or PurePosixPath(upload.filename).stem,
                content_hash=content_hash,
                mime=upload.mime,
                byte_size=len(upload.content),
                raw_uri=raw_uri,
            ),
            convert_component_version=E0_CONVERT_VERSION,
            lane=lane,
        )

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
        lane: ProcessingLane = ProcessingLane.STEADY,
    ) -> IngestedVersion:
        """Ingest one WATCHED observation of a lineage (D55).

        Identity is connector-native (source_kind, source_ref) — bytes
        cannot identify a lineage (they change; that is the premise). A
        changed file becomes a new VERSION of its lineage; identical bytes
        are the content-hash no-op.
        """
        content_hash = hashlib.sha256(upload.content).hexdigest()
        self._guard_ingest(
            deployment_id=deployment_id,
            source_kind=source_kind,
            source_ref=source_ref,
            content_hash=content_hash,
        )
        doc_id = uuid5(
            NAMESPACE_URL, f"rememberstack:{source_kind}:{deployment_id}:{source_ref}"
        )
        suffix = PurePosixPath(upload.filename).suffix
        raw_uri = f"{doc_id}/{content_hash}/original{suffix}"
        record = UploadRecord(
            deployment_id=deployment_id,
            doc_id=doc_id,
            source_kind=source_kind,
            source_ref=source_ref,
            source_uri=source_ref,
            title=upload.title or PurePosixPath(upload.filename).stem,
            content_hash=content_hash,
            mime=upload.mime,
            byte_size=len(upload.content),
            raw_uri=raw_uri,
            versioning_mode=versioning_mode,
            source_modified_at=source_modified_at,
            source_version_ref=source_version_ref,
            sync_cycle_id=sync_cycle_id,
        )
        try:
            self._raw_store.write_bytes(
                key=ObjectKey(raw_uri),
                content=upload.content,
                # D51: media a harness reads stays hot; text originals
                # kept only for audit go cold — routed at the write
                storage_class=storage_class_for(mime=upload.mime),
            )
        except ObjectAlreadyExistsError:
            pass
        return self._catalog.record_upload(
            record=record, convert_component_version=E0_CONVERT_VERSION, lane=lane
        )

    def _guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Check D74 before writing forgotten bytes back into the raw store."""
        self._admission.guard_ingest(
            deployment_id=deployment_id,
            source_kind=source_kind,
            source_ref=source_ref,
            content_hash=content_hash,
        )


class ConvertHandler:
    """The convert stage (D38/D57): raw bytes → document.md + blocks + manifest.

    One representation per run: converter output and the deterministic block
    sequence are written ID-addressed to the artifacts store, then recorded as
    an immutable `document_representations` row (D65). Chains structure.
    """

    def __init__(
        self,
        *,
        catalog: DocumentCatalog,
        raw_store: ObjectStorePort,
        artifact_store: ObjectStorePort,
        router: ConversionRouter,
    ) -> None:
        """Bind the handler to its catalog, both stores, and the route table."""
        self._catalog = catalog
        self._raw_store = raw_store
        self._artifact_store = artifact_store
        self._router = router

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Convert one document version and record its representation.

        Replay before regenerate (D65/D7): a representation this toolchain
        already produced for the version is re-chained as-is — the converter
        is never re-called on a retried or replayed attempt.
        """
        del meter
        source = self._catalog.convert_source(
            version_id=_payload_uuid(work=work, field="version_id")
        )
        try:
            converter = self._router.converter_for(mime=source.mime)
        except UnroutableMimeError as err:
            # deterministic for this input — retrying cannot help (D12); the
            # version's own status must not keep claiming in-flight work:
            self._catalog.mark_version_failed(
                version_id=source.version_id, error=str(err)
            )
            raise NonRetryableHandlerError(str(err)) from err
        existing = self._catalog.existing_representation(
            version_id=source.version_id,
            route=converter.name,
            converter_version=converter.version,
            blockizer_version=BLOCKIZER_VERSION,
        )
        if existing is not None:
            return self._structure_follow_up(
                work=work, version_id=source.version_id, representation_id=existing
            )
        content = self._raw_store.read_bytes(key=ObjectKey(source.raw_uri))
        try:
            result = converter.convert(content=content, mime=source.mime)
            _require_coherent_envelope(result=result)
        except ValidationError as err:
            # a converter that cannot build its own envelope models is a
            # deterministic converter bug, exactly like an incoherent envelope
            self._catalog.mark_version_failed(
                version_id=source.version_id, error=f"invalid envelope: {err}"
            )
            raise NonRetryableHandlerError(str(err)) from err
        except ConversionError as err:
            self._catalog.mark_version_failed(
                version_id=source.version_id, error=str(err)
            )
            raise NonRetryableHandlerError(str(err)) from err
        blocks = blockize(document_md=result.document_md)

        representation_id = uuid4()
        base = f"{source.doc_id}/{source.content_hash}/{representation_id}"
        markdown_bytes = result.document_md.encode("utf-8")
        markdown_hash = hashlib.sha256(markdown_bytes).hexdigest()
        blocks_bytes = _json_bytes(
            payload={
                "blockizer_version": BLOCKIZER_VERSION,
                "block_count": len(blocks),
                "markdown_chars": len(result.document_md),
                "blocks": [
                    block.model_dump(mode="json", exclude_none=True) for block in blocks
                ],
            }
        )
        source_map_bytes: bytes | None = None
        if result.source_map is not None:
            source_map_bytes = _json_bytes(
                payload={
                    "entries": [
                        entry.model_dump(mode="json", exclude_none=True)
                        for entry in result.source_map
                    ]
                }
            )
        asset_payloads: dict[str, bytes] = {}
        asset_inventory: list[dict[str, object]] = []
        for asset in result.derived_assets:
            asset_uri = f"{base}/media/{asset.name}"
            asset_payloads[asset_uri] = asset.content
            asset_inventory.append(
                {
                    "name": asset.name,
                    "kind": asset.kind,
                    "media_type": asset.media_type,
                    "description": asset.description,
                    "uri": asset_uri,
                    "sha256": hashlib.sha256(asset.content).hexdigest(),
                    "locators": [
                        locator.model_dump(mode="json", exclude_none=True)
                        for locator in asset.locators
                    ],
                }
            )
        manifest_bytes = _json_bytes(
            payload={
                "route": converter.name,
                "converter": {"name": converter.name, "version": converter.version},
                "blockizer_version": BLOCKIZER_VERSION,
                "components": [
                    component.model_dump(mode="json")
                    for component in result.manifest.components
                ],
                "coverage": result.manifest.coverage.model_dump(mode="json"),
                "derivation_ranges": [
                    labeled.model_dump(mode="json", exclude_none=True)
                    for labeled in result.manifest.derivation_ranges
                ],
                "tracks": [
                    track.model_dump(mode="json", exclude_none=True)
                    for track in result.manifest.tracks
                ],
                "page_dimensions": [
                    dims.model_dump(mode="json", exclude_none=True)
                    for dims in result.manifest.page_dimensions
                ],
                "markdown_sha256": markdown_hash,
                "source_map": None
                if source_map_bytes is None or result.source_map is None
                else {
                    "uri": f"{base}/source_map.json",
                    "sha256": hashlib.sha256(source_map_bytes).hexdigest(),
                    "entry_count": len(result.source_map),
                },
                "derived_assets": asset_inventory,
                "warnings": list(result.warnings),
            }
        )
        meta_bytes = _json_bytes(
            payload={
                "doc_id": str(source.doc_id),
                "version_id": str(source.version_id),
                "representation_id": str(representation_id),
                "content_hash": source.content_hash,
                "mime": source.mime,
                "title": source.title,
                "route": converter.name,
            }
        )
        artifacts = {
            f"{base}/document.md": markdown_bytes,
            f"{base}/blocks.json": blocks_bytes,
            f"{base}/conversion.json": manifest_bytes,
            f"{base}/meta.json": meta_bytes,
            **asset_payloads,
        }
        if source_map_bytes is not None:
            artifacts[f"{base}/source_map.json"] = source_map_bytes
        for uri, payload_bytes in artifacts.items():
            self._artifact_store.write_bytes(key=ObjectKey(uri), content=payload_bytes)

        self._catalog.record_representation(
            record=RepresentationRecord(
                representation_id=representation_id,
                deployment_id=source.deployment_id,
                version_id=source.version_id,
                route=converter.name,
                converter_name=converter.name,
                converter_version=converter.version,
                blockizer_version=BLOCKIZER_VERSION,
                markdown_uri=f"{base}/document.md",
                blocks_uri=f"{base}/blocks.json",
                conversion_uri=f"{base}/conversion.json",
                meta_uri=f"{base}/meta.json",
                markdown_hash=markdown_hash,
                manifest_hash=hashlib.sha256(manifest_bytes).hexdigest(),
            )
        )
        return self._structure_follow_up(
            work=work, version_id=source.version_id, representation_id=representation_id
        )

    def _structure_follow_up(
        self, *, work: ClaimedWork, version_id: UUID, representation_id: UUID
    ) -> HandlerOutcome:
        """Chain the structure stage for one (version, representation)."""
        return HandlerOutcome(
            follow_up=(
                EnqueueWork(
                    deployment_id=work.deployment_id,
                    target_kind=work.target_kind,
                    target_id=work.target_id,
                    stage=PipelineStage.STRUCTURE,
                    component_version=E0_STRUCTURE_VERSION,
                    content_hash=work.content_hash,
                    lane=work.lane,
                    payload={
                        "version_id": str(version_id),
                        "representation_id": str(representation_id),
                    },
                ),
            )
        )


class StructurerSettings(BaseSettings):
    """The D79 fallback structure-proposal seat and deterministic gate knobs.

    ``REMEMBERSTACK_STRUCTURER_*`` is retained for deployment compatibility
    but now names only the anchor-proposal fallback. ``min_blocks_for_llm`` is
    accepted as a deprecated compatibility value; parser/check routing never
    reads it. Gate metrics come from the same computation persisted as D79
    stats. These operational starting points are not stat-to-verdict rules.
    """

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_STRUCTURER_")

    model: str = Field(default="openai/gpt-5.6-luna")
    min_blocks_for_llm: int = Field(default=8, ge=1)
    max_prompt_chars: int = Field(default=200_000, ge=1_000)
    min_heading_density_per_10k: float = Field(default=0.25, ge=0)
    max_oversized_leaf_ratio: float = Field(default=0.75, ge=0, le=1)


class SkeletonCheckSettings(BaseSettings):
    """The independent D79 skeleton-check seat."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_SKELETON_CHECK_")

    model: str = Field(default="z-ai/glm-4.7-flash")


class RoleSettings(BaseSettings):
    """The narrow bounded title-only role classifier seat."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_ROLE_")

    model: str = Field(default="z-ai/glm-4.7-flash")


_FALLBACK_PROMPT: Final = """You propose section starts for a document whose \
markdown headings were insufficient. Return a hierarchical tree of anchors.

Each node has exactly:
- anchor: an EXACT non-empty substring contained wholly in one rendered block
- occurrence_index: zero-based occurrence of that exact substring inside the \
enclosing parent's block range
- children: nested nodes using the same shape

Never return character offsets, block ordinals, roles, summaries, or placement.
If no reliable internal sections exist, return an empty sections list.

Document title: {title}
Source kind: {source_kind}
Rendered blocks ({included} included, {omitted} omitted):
{blocks}"""

_CHECK_INSTRUCTION: Final = """Judge whether this document SECTION SKELETON \
is structurally coherent. You see statistics and heading lines only, never \
section body text. Return exactly one primary verdict:
coherent | incoherent_repeated_boilerplate | \
incoherent_heading_sequence | incoherent_junk_titles | \
incoherent_over_fragmented.
Do not propose structure and do not add a reason or confidence."""

_CHECK_PROMPT_TEMPLATE: Final = """{instruction}
Document title: {title}
Source kind: {source_kind}
Stats:
{stats}
Section lines: {included} included, {omitted} omitted
{lines}"""

_ROLE_INSTRUCTION: Final = """Assign one section role from this closed set:
{roles}
Use only the provided title. Return assignments keyed by the exact node_path.
Do not summarize or inspect body content."""

_CHECK_PROMPT_HASH: Final = hashlib.sha256(
    _CHECK_PROMPT_TEMPLATE.format(
        instruction=_CHECK_INSTRUCTION,
        title="{capped_document_title}",
        source_kind="{capped_source_kind}",
        stats="{canonical_stats_json}",
        included="{included_count}",
        omitted="{omitted_count}",
        lines="{ordered_sampled_lines}",
    ).encode("utf-8")
).hexdigest()
_CHECK_SCHEMA_HASH: Final = hashlib.sha256(
    json.dumps(
        SkeletonCheckResponse.model_json_schema(), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
).hexdigest()


class StructureHandler:
    """D79 parse → stats/gates → check/fallback → roles → immutable generation."""

    def __init__(
        self,
        *,
        catalog: DocumentCatalog,
        artifact_store: ObjectStorePort,
        model_provider: ModelProviderPort | None = None,
        settings: StructurerSettings | None = None,
        check_settings: SkeletonCheckSettings | None = None,
        role_settings: RoleSettings | None = None,
        summary_settings: SummarySettings | None = None,
    ) -> None:
        """Bind the handler to all four independently versioned seats."""
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._model_provider = model_provider
        self._settings = settings or StructurerSettings()
        self._check_settings = check_settings or SkeletonCheckSettings()
        self._role_settings = role_settings or RoleSettings()
        self._summarizer = SectionSummarizer(
            catalog=catalog,
            artifact_store=artifact_store,
            model_provider=model_provider,
            settings=summary_settings,
        )

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Run the acyclic D79 state machine and flip currency once."""
        source = self._catalog.structure_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        blocks_doc = json.loads(
            self._artifact_store.read_bytes(key=ObjectKey(source.blocks_uri))
        )
        markdown = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        blocks = blocks_from_sidecar(blocks_doc=blocks_doc, document_md=markdown)
        configured_check_version = _skeleton_check_version(
            settings=self._check_settings
        )
        configured_role_version = _role_version(settings=self._role_settings)
        configured_skeleton_version = _skeleton_version(settings=self._settings)
        current = self._catalog.current_section_tree(
            representation_id=source.representation_id
        )
        replay_summary = None
        if (
            current is not None
            and current.skeleton_version == configured_skeleton_version
            and current.skeleton_check_version == configured_check_version
            and current.roles_version == configured_role_version
        ):
            # A summary-seat/prompt swap copies the immutable selected
            # skeleton + role fields byte-for-byte. Parser/check/role calls
            # are skipped; only the new summary/placement generations run.
            if (
                current.summary_version == self._summarizer.version
                and current.placement_version == self._summarizer.placement_version
                and current.placement_path is not None
                and all(section.summary is not None for section in current.sections)
            ):
                selected = current.sections
                replay_summary = current
            else:
                selected = tuple(
                    section.model_copy(update={"summary": None})
                    for section in current.sections
                )
            route = current.route_tag
            selection_analysis = analyze_skeleton(
                sections=selected, blocks=blocks, markdown_chars=len(markdown)
            )
            selection_candidate_hash = current.candidate_skeleton_hash
            selecting_check_id = current.selecting_check_id
            check_version = current.skeleton_check_version
            producer_family = current.skeleton_producer_family
        else:
            parsed = parse_heading_skeleton(
                blocks=blocks,
                title=source.title,
                markdown_chars=blocks_doc["markdown_chars"],
            )
            parsed_analysis = analyze_skeleton(
                sections=parsed, blocks=blocks, markdown_chars=len(markdown)
            )
            parsed_hash = skeleton_hash(sections=parsed)

            selected = parsed
            selection_analysis = parsed_analysis
            selection_candidate_hash = parsed_hash
            selecting_check_id = None
            check_version = None
            producer_family = "N/A"

            if (
                parsed_analysis.heading_density
                < self._settings.min_heading_density_per_10k
            ):
                (
                    route,
                    selected,
                    selection_analysis,
                    selection_candidate_hash,
                    selecting_check_id,
                    producer_family,
                ) = self._fallback_with_terminal(
                    source=source,
                    work=work,
                    blocks=blocks,
                    markdown=markdown,
                    meter=meter,
                    parsed_root=parsed[0],
                    kept_route=StructureRouteTag.FALLBACK_DENSITY,
                )
                check_version = configured_check_version
            elif (
                parsed_analysis.oversized_leaf_ratio
                > self._settings.max_oversized_leaf_ratio
            ):
                (
                    route,
                    selected,
                    selection_analysis,
                    selection_candidate_hash,
                    selecting_check_id,
                    producer_family,
                ) = self._fallback_with_terminal(
                    source=source,
                    work=work,
                    blocks=blocks,
                    markdown=markdown,
                    meter=meter,
                    parsed_root=parsed[0],
                    kept_route=StructureRouteTag.FALLBACK_LEAF,
                )
                check_version = configured_check_version
            else:
                initial_outcome, initial_check_id = self._check(
                    source=source,
                    work=work,
                    sections=parsed,
                    analysis=parsed_analysis,
                    meter=meter,
                    terminal=False,
                )
                selecting_check_id = initial_check_id
                check_version = configured_check_version
                if _is_incoherent(outcome=initial_outcome):
                    self._persist_generation(
                        source=source,
                        sections=parsed,
                        blocks=blocks,
                        markdown=markdown,
                        route=StructureRouteTag.PARSER_DEMOTED_CHECK,
                        analysis=parsed_analysis,
                        candidate_skeleton_hash=parsed_hash,
                        selecting_check_id=initial_check_id,
                        check_version=configured_check_version,
                        roles_version=None,
                        summary_version=None,
                        placement_version=None,
                        placement_path=None,
                        summary_cache_keys={},
                        producer_family="N/A",
                        make_current=False,
                    )
                    (
                        route,
                        selected,
                        selection_analysis,
                        selection_candidate_hash,
                        selecting_check_id,
                        producer_family,
                    ) = self._fallback_with_terminal(
                        source=source,
                        work=work,
                        blocks=blocks,
                        markdown=markdown,
                        meter=meter,
                        parsed_root=parsed[0],
                        kept_route=StructureRouteTag.FALLBACK_AFTER_CHECK,
                    )
                else:
                    route = StructureRouteTag.PARSER

            if (
                current is not None
                and current.route_tag == route
                and current.skeleton_version == configured_skeleton_version
                and current.skeleton_hash == skeleton_hash(sections=selected)
                and current.roles_version == configured_role_version
                and current.summary_version == self._summarizer.version
                and current.placement_version == self._summarizer.placement_version
                and current.placement_path is not None
                and all(section.summary is not None for section in current.sections)
            ):
                # A checker-only bump already appended its check record. If it
                # kept the route and selected tree, the complete current seats
                # remain authoritative: no roles/summaries or pointer write.
                return _chunk_outcome(work=work, source=source)

            selected = self._assign_roles(sections=selected, meter=meter)

        if replay_summary is not None:
            replay_placement = replay_summary.placement_path
            if replay_placement is None:
                raise AssertionError(
                    "a complete summary generation must carry root placement"
                )
            summary_result = self._summarizer.replay(
                source=source,
                sections=selected,
                placement_path=replay_placement,
                blocks=blocks,
                markdown=markdown,
            )
        else:
            summary_result = self._summarizer.summarize(
                source=source,
                sections=selected,
                blocks=blocks,
                markdown=markdown,
                meter=meter,
            )
        self._persist_generation(
            source=source,
            sections=summary_result.sections,
            blocks=blocks,
            markdown=markdown,
            route=route,
            analysis=selection_analysis,
            candidate_skeleton_hash=selection_candidate_hash,
            selecting_check_id=selecting_check_id,
            check_version=check_version,
            roles_version=configured_role_version,
            summary_version=summary_result.summary_version,
            placement_version=summary_result.placement_version,
            placement_path=summary_result.placement_path,
            summary_cache_keys=summary_result.cache_keys,
            producer_family=producer_family,
            make_current=True,
        )
        return _chunk_outcome(work=work, source=source)

    def _fallback_with_terminal(
        self,
        *,
        source: StructureSource,
        work: ClaimedWork,
        blocks: tuple[Block, ...],
        markdown: str,
        meter: CostMeterPort,
        parsed_root: SnappedSection,
        kept_route: StructureRouteTag,
    ) -> tuple[
        StructureRouteTag, tuple[SnappedSection, ...], SkeletonAnalysis, str, UUID, str
    ]:
        """Every fallback tree faces the terminal judge (§4.1 state machine).

        The plausible-wrong-tree failure mode does not care which gate demoted
        the document, so density/leaf demotions get the same terminal check as
        incoherent-verdict demotions. Terminal incoherence degrades to the
        synthetic root — honest no-structure over a plausible-wrong tree; the
        check-record chain preserves which gate started the demotion. The
        returned analysis/candidate hash always describe the SELECTED tree —
        generation rows describe what is persisted, check records describe the
        candidates they judged.
        """
        fallback, producer_family = self._fallback(
            source=source, blocks=blocks, markdown=markdown, meter=meter
        )
        fallback_analysis = analyze_skeleton(
            sections=fallback, blocks=blocks, markdown_chars=len(markdown)
        )
        terminal_outcome, terminal_check_id = self._check(
            source=source,
            work=work,
            sections=fallback,
            analysis=fallback_analysis,
            meter=meter,
            terminal=True,
        )
        if _is_incoherent(outcome=terminal_outcome):
            synthetic = (parsed_root,)
            return (
                StructureRouteTag.SYNTHETIC_AFTER_CHECK,
                synthetic,
                analyze_skeleton(
                    sections=synthetic, blocks=blocks, markdown_chars=len(markdown)
                ),
                skeleton_hash(sections=synthetic),
                terminal_check_id,
                "N/A",
            )
        return (
            kept_route,
            fallback,
            fallback_analysis,
            skeleton_hash(sections=fallback),
            terminal_check_id,
            producer_family,
        )

    def _fallback(
        self,
        *,
        source: StructureSource,
        blocks: tuple[Block, ...],
        markdown: str,
        meter: CostMeterPort,
    ) -> tuple[tuple[SnappedSection, ...], str]:
        """Return the resolved proposal and actual family; failures are N/A roots."""
        if self._model_provider is None:
            return (
                resolve_fallback_skeleton(
                    proposed=(), blocks=blocks, document_md=markdown, title=source.title
                ),
                "N/A",
            )
        prompt = _render_fallback_prompt(
            source=source,
            blocks=blocks,
            markdown=markdown,
            ceiling=self._settings.max_prompt_chars,
        )
        try:
            generated = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.model, prompt=prompt, temperature=0.0
                ),
                response_type=FallbackStructureResponse,
            )
        except ProviderAccountingError:
            raise
        except ProviderCallError as error:
            if error.usage is not None:
                meter.record(
                    call_key="structure_fallback_failure",
                    tier="fallback_failed_response",
                    usage=error.usage,
                    outcome="provider_error",
                )
            return (
                resolve_fallback_skeleton(
                    proposed=(), blocks=blocks, document_md=markdown, title=source.title
                ),
                "N/A",
            )
        except Exception:  # noqa: BLE001 — a document never fails structuring
            return (
                resolve_fallback_skeleton(
                    proposed=(), blocks=blocks, document_md=markdown, title=source.title
                ),
                "N/A",
            )
        meter.record(
            call_key="structure_fallback",
            tier="structure_fallback",
            usage=generated.usage,
        )
        return (
            resolve_fallback_skeleton(
                proposed=generated.output.sections,
                blocks=blocks,
                document_md=markdown,
                title=source.title,
            ),
            _model_family(model=generated.usage.model_name),
        )

    def _check(
        self,
        *,
        source: StructureSource,
        sections: tuple[SnappedSection, ...],
        analysis: SkeletonAnalysis,
        work: ClaimedWork,
        meter: CostMeterPort,
        terminal: bool,
    ) -> tuple[SkeletonCheckOutcome, UUID]:
        """Append one explicit checker outcome; failures are fail-open."""
        outcome = SkeletonCheckOutcome.NOT_RUN_SHORT
        usage: ProviderCallUsage | None = None
        failure: dict[str, str | int | float | bool | None] | None = None
        # A prompt no call will ever send gets no render and no hash — a
        # not_run_short record with a sampled_input_hash would bookkeep an
        # input that never existed (and rendering is the expensive part).
        sampled_hash: str | None = None
        if len(sections) - 1 >= MIN_CHECK_SECTIONS:
            prompt = _render_check_prompt(
                source=source, sections=sections, analysis=analysis
            )
            sampled_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if self._model_provider is None:
                outcome = SkeletonCheckOutcome.PROVIDER_ERROR
                failure = {"error_type": "provider_unavailable", "has_usage": False}
            else:
                try:
                    generated = self._model_provider.generate(
                        request=ModelRequest(
                            model=self._check_settings.model,
                            prompt=prompt,
                            temperature=0.0,
                        ),
                        response_type=SkeletonCheckResponse,
                    )
                    usage = generated.usage
                    outcome = SkeletonCheckOutcome(generated.output.verdict)
                except ProviderAccountingError:
                    raise
                except (
                    ProviderInvalidResponseError,
                    # ValueError covers pydantic ValidationError AND the
                    # off-enum verdict coercion from a non-OpenRouter
                    # provider — both are invalid responses, not outages
                    ValueError,
                ) as error:
                    outcome = SkeletonCheckOutcome.INVALID_RESPONSE
                    usage = getattr(error, "usage", None) or usage
                    failure = _failure_envelope(error=error, usage=usage)
                except ProviderCallError as error:
                    outcome = SkeletonCheckOutcome.PROVIDER_ERROR
                    usage = error.usage
                    failure = _failure_envelope(error=error, usage=usage)
                except Exception as error:  # noqa: BLE001 - guard is fail-open
                    outcome = SkeletonCheckOutcome.PROVIDER_ERROR
                    failure = _failure_envelope(error=error, usage=None)

        call_key = "skeleton_check_terminal" if terminal else "skeleton_check"
        if usage is not None:
            meter.record(call_key=call_key, tier="skeleton_check", usage=usage)
        check_id = uuid4()
        checker_model = (
            usage.model_name if usage is not None else self._check_settings.model
        )
        self._catalog.record_skeleton_check(
            record=SkeletonCheckRecord(
                check_id=check_id,
                processing_id=work.processing_id,
                deployment_id=source.deployment_id,
                doc_id=source.doc_id,
                version_id=source.version_id,
                representation_id=source.representation_id,
                candidate_skeleton_hash=skeleton_hash(sections=sections),
                stats_version=SKELETON_STATS_VERSION,
                stats=analysis.stats,
                sampled_input_hash=sampled_hash,
                check_outcome=outcome,
                checker_component_version=E0_SKELETON_CHECK_VERSION,
                checker_model=checker_model,
                checker_model_hash=hashlib.sha256(
                    checker_model.encode("utf-8")
                ).hexdigest(),
                checker_prompt_hash=_CHECK_PROMPT_HASH,
                checker_schema_hash=_CHECK_SCHEMA_HASH,
                provider_failure=failure,
                tokens_in=usage.tokens_in if usage is not None else None,
                tokens_out=usage.tokens_out if usage is not None else None,
                cost_usd=usage.cost_usd if usage is not None else None,
                latency_ms=usage.latency_ms if usage is not None else None,
            )
        )
        return outcome, check_id

    def _assign_roles(
        self, *, sections: tuple[SnappedSection, ...], meter: CostMeterPort
    ) -> tuple[SnappedSection, ...]:
        """Rules first, one bounded title-only call second, explicit body last."""
        roles: dict[str, str] = {"0": "body"}
        undecided: list[SnappedSection] = []
        for section in sections[1:]:
            role = deterministic_section_role(normalized_title=section.normalized_title)
            if role is None:
                undecided.append(section)
            else:
                roles[section.node_path] = role

        included, prompt = _render_role_prompt(sections=tuple(undecided))
        if included and self._model_provider is not None:
            try:
                generated = self._model_provider.generate(
                    request=ModelRequest(
                        model=self._role_settings.model, prompt=prompt, temperature=0.0
                    ),
                    response_type=RoleClassificationResponse,
                )
                meter.record(
                    call_key="section_roles",
                    tier="title_classifier",
                    usage=generated.usage,
                )
                allowed = {section.node_path for section in included}
                for assignment in generated.output.assignments:
                    if (
                        assignment.node_path in allowed
                        and assignment.node_path not in roles
                    ):
                        roles[assignment.node_path] = assignment.role
            except ProviderAccountingError:
                raise
            except ProviderCallError as error:
                if error.usage is not None:
                    meter.record(
                        call_key="section_roles_failure",
                        tier="title_classifier_failed_response",
                        usage=error.usage,
                        outcome="provider_error",
                    )
            except Exception:  # noqa: BLE001 - undecided titles become body
                pass
        return tuple(
            section.model_copy(update={"role": roles.get(section.node_path, "body")})
            for section in sections
        )

    def _persist_generation(
        self,
        *,
        source: StructureSource,
        sections: tuple[SnappedSection, ...],
        blocks: tuple[Block, ...],
        markdown: str,
        route: StructureRouteTag,
        analysis: SkeletonAnalysis,
        candidate_skeleton_hash: str,
        selecting_check_id: UUID | None,
        check_version: str | None,
        roles_version: str | None,
        summary_version: str | None,
        placement_version: str | None,
        placement_path: str | None,
        summary_cache_keys: dict[str, str],
        producer_family: str,
        make_current: bool,
    ) -> None:
        """Append one generation, then write its versioned sidecar.

        The generation identity is selected-input and seat identity only,
        never checker version or provider output. A checker bump over an
        unchanged route+tree derives the same id, while a route/tree or seat
        change appends a generation. Degraded markers still re-mint repairs.
        """
        skeleton_version = _skeleton_version(settings=self._settings)
        generation_id = uuid5(
            NAMESPACE_URL,
            "rememberstack:structure:"
            f"{source.representation_id}:{E0_STRUCTURE_VERSION}:"
            f"{route.value}:{skeleton_hash(sections=sections)}:"
            f"{skeleton_version}:{roles_version or 'none'}:"
            f"{summary_version or 'degraded'}:"
            f"{placement_version or 'degraded'}",
        )
        sidecar_key = (
            source.blocks_uri.rsplit("/", 1)[0]
            + f"/structure/{generation_id}/pageindex.json"
        )
        persisted = self._catalog.record_section_tree(
            record=SectionTreeRecord(
                deployment_id=source.deployment_id,
                doc_id=source.doc_id,
                version_id=source.version_id,
                representation_id=source.representation_id,
                structure_generation_id=generation_id,
                sections=sections,
                placement_path=placement_path,
                structurer_name=route.value,
                structurer_version=E0_STRUCTURE_VERSION,
                skeleton_version=skeleton_version,
                skeleton_hash=skeleton_hash(sections=sections),
                skeleton_producer_family=producer_family,
                skeleton_check_version=check_version,
                roles_version=roles_version,
                summary_version=summary_version,
                placement_version=placement_version,
                selecting_check_id=selecting_check_id,
                route_tag=route,
                candidate_skeleton_hash=candidate_skeleton_hash,
                stats_version=SKELETON_STATS_VERSION,
                stats=analysis.stats,
                pageindex_uri=sidecar_key,
                make_current=make_current,
            )
        )
        persisted_cache_keys = summary_cache_keys
        if persisted.sections != sections or persisted.placement_path != placement_path:
            if persisted.placement_path is not None and all(
                section.summary is not None for section in persisted.sections
            ):
                persisted_cache_keys = self._summarizer.replay(
                    source=source,
                    sections=persisted.sections,
                    placement_path=persisted.placement_path,
                    blocks=blocks,
                    markdown=markdown,
                ).cache_keys
            else:
                # A concurrent first write with different partial output did
                # not necessarily see the prompts keyed by this attempt.
                persisted_cache_keys = {}
        payload = _json_bytes(
            payload={
                "structure_generation_id": str(persisted.structure_generation_id),
                "structurer_version": persisted.structurer_version,
                "generations": {
                    "skeleton": persisted.skeleton_version,
                    "skeleton_check": persisted.skeleton_check_version,
                    "roles": persisted.roles_version,
                    "summary": persisted.summary_version,
                    "placement": persisted.placement_version,
                },
                "route_tag": persisted.route_tag.value,
                "selecting_check_id": (
                    str(persisted.selecting_check_id)
                    if persisted.selecting_check_id is not None
                    else None
                ),
                "candidate_skeleton_hash": persisted.candidate_skeleton_hash,
                "stats_version": persisted.stats_version,
                "stats": persisted.stats.model_dump(mode="json"),
                "placement": persisted.placement_path,
                "sections": [
                    {
                        **section.model_dump(mode="json"),
                        "summary_cache_key": persisted_cache_keys.get(
                            section.node_path
                        ),
                    }
                    for section in persisted.sections
                ],
            }
        )
        try:
            self._artifact_store.write_bytes(
                key=ObjectKey(persisted.pageindex_uri), content=payload
            )
        except ObjectAlreadyExistsError:
            pass


def _chunk_outcome(*, work: ClaimedWork, source: StructureSource) -> HandlerOutcome:
    """Continue the selected representation into deterministic chunking."""
    return HandlerOutcome(
        follow_up=(
            EnqueueWork(
                deployment_id=work.deployment_id,
                target_kind=work.target_kind,
                target_id=work.target_id,
                stage=PipelineStage.CHUNK,
                component_version=E1_CHUNK_VERSION,
                content_hash=work.content_hash,
                lane=work.lane,
                payload={
                    "version_id": str(source.version_id),
                    "representation_id": str(source.representation_id),
                },
            ),
        )
    )


def _render_fallback_prompt(
    *, source: StructureSource, blocks: tuple[Block, ...], markdown: str, ceiling: int
) -> str:
    """Render whole block excerpts under the compatibility prompt ceiling."""
    rendered: list[str] = []
    for block in blocks:
        raw = markdown[block.char_start : block.char_end]
        line = f"[{block.type.value}]\n{raw}"
        trial = "\n\n".join((*rendered, line))
        prompt = _FALLBACK_PROMPT.format(
            title=(source.title or "(untitled)")[:LONG_TITLE],
            source_kind=source.source_kind[:LONG_TITLE],
            included=len(rendered) + 1,
            omitted=len(blocks) - len(rendered) - 1,
            blocks=trial,
        )
        if len(prompt) > ceiling:
            break
        rendered.append(line)
    return _FALLBACK_PROMPT.format(
        title=(source.title or "(untitled)")[:LONG_TITLE],
        source_kind=source.source_kind[:LONG_TITLE],
        included=len(rendered),
        omitted=len(blocks) - len(rendered),
        blocks="\n\n".join(rendered),
    )


def _render_check_prompt(
    *,
    source: StructureSource,
    sections: tuple[SnappedSection, ...],
    analysis: SkeletonAnalysis,
) -> str:
    """Anomaly-first deterministic sampling, rendered back in document order.

    Linear, not quadratic: every candidate line renders exactly once, the
    static envelope (stats block, header) renders exactly once, and the
    greedy fit tracks lengths arithmetically — the previous shape re-rendered
    the full prompt per candidate, ~5s of pure CPU on a 6,000-heading
    document. The fit is conservative (worst-case count digits, a newline
    per line), so the final render can only land under the ceiling; the
    assert is the belt to that suspenders.
    """
    candidates = sections[1:]
    priorities = _anomaly_priority(sections=candidates, analysis=analysis)
    line_for = tuple(
        _check_line(
            section=section,
            direct_body_chars=analysis.direct_body_chars[section.node_path],
        )
        for section in candidates
    )
    stats_json = json.dumps(
        analysis.stats.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    def compose(ordered: tuple[int, ...]) -> str:
        return _CHECK_PROMPT_TEMPLATE.format(
            instruction=_CHECK_INSTRUCTION,
            title=(source.title or "(untitled)")[:LONG_TITLE],
            source_kind=source.source_kind[:LONG_TITLE],
            stats=stats_json,
            included=len(ordered),
            omitted=len(candidates) - len(ordered),
            lines="\n".join(line_for[index] for index in ordered),
        )

    overhead = len(compose(()))
    budget = SKELETON_CHECK_PROMPT_CEILING - overhead
    selected: set[int] = set()
    used = 0
    for index in priorities:
        cost = len(line_for[index]) + 1  # +1: the joining newline, worst case
        if used + cost <= budget:
            selected.add(index)
            used += cost
    rendered = compose(tuple(sorted(selected)))
    if len(rendered) > SKELETON_CHECK_PROMPT_CEILING:
        raise AssertionError("checker prompt base exceeds its versioned hard ceiling")
    return rendered


def _anomaly_priority(
    *, sections: tuple[SnappedSection, ...], analysis: SkeletonAnalysis
) -> tuple[int, ...]:
    """Repeated titles, jumps, tiny bodies, large leaves, then head/tail."""
    priorities: list[int] = []

    def add(indexes: Iterable[int]) -> None:
        for index in indexes:
            if index not in priorities:
                priorities.append(index)

    multiplicity: dict[str, int] = {}
    for section in sections:
        multiplicity[section.normalized_title] = (
            multiplicity.get(section.normalized_title, 0) + 1
        )
    add(
        tuple(
            index
            for index, section in sorted(
                enumerate(sections),
                key=lambda item: (-multiplicity[item[1].normalized_title], item[0]),
            )
            if multiplicity[section.normalized_title] > 1
        )[:8]
    )
    jumps = sorted(
        (
            (
                # raw levels only — a pair missing a raw level is no jump,
                # matching the d79-v2 stat rule (tree depth would fake zero
                # or invent jumps that steer the sample)
                max(0, right.heading_level - left.heading_level - 1)
                if left.heading_level is not None and right.heading_level is not None
                else 0,
                index + 1,
            )
            for index, (left, right) in enumerate(
                zip(sections, sections[1:], strict=False)
            )
        ),
        key=lambda item: (-item[0], item[1]),
    )
    add(tuple(index for jump, index in jumps if jump > 0)[:8])
    add(
        tuple(
            index
            for index, _ in sorted(
                enumerate(sections),
                key=lambda item: (
                    analysis.direct_body_chars[item[1].node_path],
                    item[0],
                ),
            )
        )[:8]
    )
    add(
        tuple(
            index
            for index, section in sorted(
                enumerate(sections),
                key=lambda item: (
                    -analysis.leaf_span_chars.get(item[1].node_path, -1),
                    item[0],
                ),
            )
            if section.node_path in analysis.leaf_span_chars
        )[:8]
    )
    add(range(min(8, len(sections))))
    add(range(max(0, len(sections) - 8), len(sections)))
    add(range(len(sections)))
    return tuple(priorities)


def _check_line(*, section: SnappedSection, direct_body_chars: int) -> str:
    title = section.title
    if len(title) > LONG_TITLE:
        title = title[: LONG_TITLE - 1] + "…"
    # anchor-derived sections carry no raw level; "null" tells the judge the
    # truth instead of a tree depth masquerading as one
    level = "null" if section.heading_level is None else str(section.heading_level)
    return (
        f"(level={level}, "
        f"title={json.dumps(title, ensure_ascii=False)}, "
        f"direct_body_chars={direct_body_chars})"
    )


def _render_role_prompt(
    *, sections: tuple[SnappedSection, ...]
) -> tuple[tuple[SnappedSection, ...], str]:
    included: list[SnappedSection] = []
    instruction = _ROLE_INSTRUCTION.format(roles=", ".join(sorted(SECTION_ROLES)))
    for section in sections:
        line = (
            f"{section.node_path}\t"
            f"{json.dumps(section.title[:LONG_TITLE], ensure_ascii=False)}"
        )
        trial = "\n".join(
            (
                instruction,
                f"Title lines: {len(included) + 1} included,"
                f" {len(sections) - len(included) - 1} omitted",
                *(
                    f"{item.node_path}\t"
                    f"{json.dumps(item.title[:LONG_TITLE], ensure_ascii=False)}"
                    for item in included
                ),
                line,
            )
        )
        if len(trial) > ROLE_PROMPT_CEILING:
            break
        included.append(section)
    prompt = "\n".join(
        (
            instruction,
            f"Title lines: {len(included)} included,"
            f" {len(sections) - len(included)} omitted",
            *(
                f"{item.node_path}\t"
                f"{json.dumps(item.title[:LONG_TITLE], ensure_ascii=False)}"
                for item in included
            ),
        )
    )
    return tuple(included), prompt


def _failure_envelope(
    *, error: Exception, usage: ProviderCallUsage | None
) -> dict[str, str | int | float | bool | None]:
    """Metadata-only failure fingerprint; never persist exception/completion text."""
    return {
        "error_type": type(error).__name__,
        "error_fingerprint": hashlib.sha256(str(error).encode("utf-8")).hexdigest(),
        "has_usage": usage is not None,
    }


def _is_incoherent(*, outcome: SkeletonCheckOutcome) -> bool:
    return outcome.value.startswith("incoherent_")


def _model_family(*, model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else model


def _skeleton_version(*, settings: StructurerSettings) -> str:
    return (
        f"{E0_SKELETON_VERSION}"
        f":density{settings.min_heading_density_per_10k:g}"
        f":leaf{settings.max_oversized_leaf_ratio:g}"
        f":fallback-model-{hashlib.sha256(settings.model.encode()).hexdigest()[:12]}"
    )


def _role_version(*, settings: RoleSettings) -> str:
    """Bind the independently configured classifier model into role provenance."""
    model_hash = hashlib.sha256(settings.model.encode("utf-8")).hexdigest()[:12]
    return f"{E0_ROLE_VERSION}:model-{model_hash}"


def _skeleton_check_version(*, settings: SkeletonCheckSettings) -> str:
    """Bind the checker seat model into its generation slot."""
    model_hash = hashlib.sha256(settings.model.encode("utf-8")).hexdigest()[:12]
    return f"{E0_SKELETON_CHECK_VERSION}:model-{model_hash}"


def _payload_uuid(*, work: ClaimedWork, field: str) -> UUID:
    """Read a required UUID from the claimed payload; absence is non-retryable."""
    value = (work.payload or {}).get(field)
    if not isinstance(value, str):
        raise NonRetryableHandlerError(
            f"stage {work.stage} work {work.processing_id} carries no {field!r} payload"
        )
    return UUID(value)


def _json_bytes(*, payload: dict[str, object]) -> bytes:
    """Serialize one artifact JSON document deterministically."""
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _require_coherent_envelope(*, result: ConversionResult) -> None:
    """Reject a converter envelope that does not fit its own text (D65/§5).

    Deterministic for the given bytes and converter version, so a violation is
    a converter bug: it dead-letters exactly like any other ConversionError.
    """
    length = len(result.document_md)
    ranges = result.manifest.derivation_ranges
    if not ranges:
        if length:
            raise ConversionError(
                "derivation labeling is total (§5): a non-empty document.md "
                "must be fully labeled"
            )
    else:
        contiguous = ranges[0].start == 0 and ranges[-1].end == length
        if contiguous:
            contiguous = all(
                following.start == labeled.end
                for labeled, following in zip(ranges, ranges[1:], strict=False)
            )
        if not contiguous:
            raise ConversionError(
                "derivation ranges must cover document.md exactly, "
                "contiguously from 0 to its length"
            )
    for entry in result.source_map or ():
        if entry.end > length:
            raise ConversionError(
                "source map entry extends past the end of document.md"
            )
    names = [asset.name for asset in result.derived_assets]
    if len(names) != len(set(names)):
        raise ConversionError("derived asset names must be unique")
