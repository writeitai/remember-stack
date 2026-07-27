"""The E0 chain (D36): upload → ingest → convert → structure.

The upload connector performs ingest synchronously (bytes to the raw store,
rows + convert work atomically through the catalog); convert and structure are
queued stage handlers. Artifacts land ID-addressed in the artifacts store
(`<doc_id>/<content_hash>/<representation_id>/…`, D37/D65); Postgres carries
only the index.

The structure stage runs the full D39 route when a model provider is
composed: the structurer LLM proposes a section tree + placement hint from
`document.md`, the deterministic snap (`core/section_snap.py`) normalizes it
onto the block grid, and the tree lands as `document_sections` rows plus the
`pageindex.json` sidecar. Every degradation path — no provider, a short
document, a failed or malformed LLM call — falls back to the synthetic root:
a document never fails structuring.
"""

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
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import ConversionRouter
from rememberstack.core import SECTION_ROLES
from rememberstack.core import snap_sections
from rememberstack.core import storage_class_for
from rememberstack.model import Block
from rememberstack.model import ClaimedWork
from rememberstack.model import ConversionError
from rememberstack.model import DocumentUpload
from rememberstack.model import EnqueueWork
from rememberstack.model import IngestedVersion
from rememberstack.model import ModelRequest
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import RepresentationRecord
from rememberstack.model import SectionTreeRecord
from rememberstack.model import SnappedSection
from rememberstack.model import StructureResponse
from rememberstack.model import StructureSource
from rememberstack.model import UnroutableMimeError
from rememberstack.model import UploadRecord
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.e1 import E1_CHUNK_VERSION

E0_CONVERT_VERSION: Final = "e0-convert-2026.07"
"""The convert sub-worker's component version (D12 idempotency key member)."""

E0_STRUCTURE_VERSION: Final = "e0-structure-2026.07c:temp0-1"
"""The structure stage's component version (D39): LLM route + snap algorithm.
07c pins temperature=0.0 — generation parameters are part of provenance."""

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
            record=UploadRecord(
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
            ),
            convert_component_version=E0_CONVERT_VERSION,
            lane=lane,
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
                "blocks": [block.model_dump(mode="json") for block in blocks],
            }
        )
        manifest_bytes = _json_bytes(
            payload={
                "route": converter.name,
                "converter": {"name": converter.name, "version": converter.version},
                "blockizer_version": BLOCKIZER_VERSION,
                "execution": "library-local",
                "markdown_sha256": markdown_hash,
                "source_map": None,
                "derived_assets": [],
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
        }
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
    """The full structure route's knobs (D39/D70 port defaults; D22 numbers).

    The model seat follows the D70 principle — a per-deployment port
    configuration, defaulting to the extraction tier. The thresholds are
    starting points to be measured: a document below ``min_blocks_for_llm``
    is served by the synthetic root alone (the LLM cannot improve a
    three-paragraph note), and the prompt reads at most
    ``max_prompt_chars`` of `document.md`.
    """

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_STRUCTURER_")

    model: str = Field(default="openai/gpt-5.6-luna")
    min_blocks_for_llm: int = Field(default=8, ge=1)
    max_prompt_chars: int = Field(default=200_000, ge=1_000)


_STRUCTURE_PROMPT: Final = """You are a document structurer. Read the document \
and propose its hierarchical section tree (a table of contents with spans).

Rules:
- Return sections as a JSON tree: each node has "title", "role", \
"char_start", "char_end", "summary" (one line), and "children" (nested nodes).
- Character offsets index into EXACTLY the document text below (0-based; \
char_end is exclusive). Top-level sections should cover the document in order.
- "role" must be one of: {roles}.
- Also return "placement": a proposed corpus path for this document, e.g. \
"/finance/annual-reports/2023/" — where it would live in an ideal directory \
tree of a whole document collection. Advisory only.
- Do not invent sections a short flat document does not have; return an empty \
"sections" list if the document has no internal structure worth naming.

Document title: {title}

DOCUMENT:
{document}"""


class StructureHandler:
    """The structure stage (D39): the full PageIndex-style route, snap-guarded.

    With a composed model provider and a long-enough document, the LLM
    proposes the section tree and placement hint; the deterministic snap
    normalizes it onto the block grid; rows + `pageindex.json` sidecar land
    and the chain completes — representation ready, live-reading pointer
    set, lineage currency moved (D54). Without a provider — or when the
    call fails or returns nothing usable — the document gets the synthetic
    root: structuring never fails a document.
    """

    def __init__(
        self,
        *,
        catalog: DocumentCatalog,
        artifact_store: ObjectStorePort,
        model_provider: ModelProviderPort | None = None,
        settings: StructurerSettings | None = None,
    ) -> None:
        """Bind the handler to its catalog, artifacts bucket, and model seat."""
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._model_provider = model_provider
        self._settings = settings or StructurerSettings()

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Structure one representation and flip currency."""
        source = self._catalog.structure_source(
            representation_id=_payload_uuid(work=work, field="representation_id")
        )
        blocks_doc = json.loads(
            self._artifact_store.read_bytes(key=ObjectKey(source.blocks_uri))
        )
        blocks = tuple(
            Block.model_validate(payload) for payload in blocks_doc["blocks"]
        )
        response = self._propose(source=source, block_count=len(blocks), meter=meter)
        proposed = response.sections if response is not None else ()
        placement = (response.placement or None) if response is not None else None
        sections = snap_sections(
            proposed=proposed,
            blocks=blocks,
            title=source.title,
            markdown_chars=blocks_doc["markdown_chars"],
        )
        structurer_name = "pageindex_llm" if len(sections) > 1 else "synthetic_root"
        persisted = self._catalog.record_section_tree(
            record=SectionTreeRecord(
                deployment_id=source.deployment_id,
                doc_id=source.doc_id,
                version_id=source.version_id,
                representation_id=source.representation_id,
                sections=sections,
                placement_path=placement,
                structurer_name=structurer_name,
                structurer_version=E0_STRUCTURE_VERSION,
            )
        )
        # the sidecar is derived from what the database actually persisted —
        # a retry that lost to an earlier attempt must not write a fresher
        # LLM proposal beside rows carrying the first one (Codex review):
        self._write_sidecar(
            source=source,
            sections=persisted.sections,
            placement=persisted.placement_path,
        )
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

    def _propose(
        self, *, source: StructureSource, block_count: int, meter: CostMeterPort
    ) -> StructureResponse | None:
        """Ask the structurer LLM for a tree; every failure degrades to None."""
        if self._model_provider is None:
            return None
        if block_count < self._settings.min_blocks_for_llm:
            return None  # a short document: the synthetic root serves it
        markdown = self._artifact_store.read_bytes(
            key=ObjectKey(source.markdown_uri)
        ).decode("utf-8")
        prompt = _STRUCTURE_PROMPT.format(
            roles=", ".join(sorted(SECTION_ROLES)),
            title=source.title or "(untitled)",
            document=markdown[: self._settings.max_prompt_chars],
        )
        try:
            generated = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.model, prompt=prompt, temperature=0.0
                ),
                response_type=StructureResponse,
            )
        except ProviderAccountingError:
            raise  # budget enforcement must never degrade missing usage to zero
        except ProviderCallError as error:
            if error.usage is not None:
                meter.record(
                    call_key="structure_failure",
                    tier="failed_response",
                    usage=error.usage,
                )
            return None
        except Exception:  # noqa: BLE001 — a document never fails structuring
            return None
        meter.record(call_key="structure", tier="structure", usage=generated.usage)
        return generated.output

    def _write_sidecar(
        self,
        *,
        source: StructureSource,
        sections: tuple[SnappedSection, ...],
        placement: str | None,
    ) -> None:
        """Write the reproducible `pageindex.json` next to `document.md` (D39)."""
        sidecar_key = source.blocks_uri.rsplit("/", 1)[0] + "/pageindex.json"
        payload = _json_bytes(
            payload={
                "structurer_version": E0_STRUCTURE_VERSION,
                "placement": placement,
                "sections": [section.model_dump(mode="json") for section in sections],
            }
        )
        try:
            self._artifact_store.write_bytes(
                key=ObjectKey(sidecar_key), content=payload
            )
        except ObjectAlreadyExistsError:
            pass  # a retried attempt replays; the first write is the truth


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
