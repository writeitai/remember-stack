"""E2 occurrence provenance: manifest-derived stamps, missing metadata fails closed."""

import hashlib
import json
import re
from typing import cast
from uuid import UUID
from uuid import uuid4

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimRecord
from rememberstack.model import DecisionRecord
from rememberstack.model import EvidenceSpan
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import SectionSpan
from rememberstack.model.conversion import DerivationRange
from rememberstack.model.conversion import ImageRegionLocator
from rememberstack.model.conversion import NormalizedRegion
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.model.occurrence_provenance import OccurrenceProvenance
from rememberstack.model.occurrence_provenance import ProvenanceMetadataMissingError
from rememberstack.model.occurrence_provenance import ReusedClaimAnchor
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.workers import E2Settings
from rememberstack.workers.e2 import ExtractClaimsHandler

_DEPLOYMENT = UUID("84000000-0000-0000-0000-000000000001")
_DOC = UUID("84000000-0000-0000-0000-000000000002")
_CHUNK = UUID("84000000-0000-0000-0000-000000000003")
_PRIOR = UUID("84000000-0000-0000-0000-000000000004")
_VERSION = UUID("84000000-0000-0000-0000-000000000005")
_REPR = UUID("84000000-0000-0000-0000-000000000006")
_SECTION = UUID("84000000-0000-0000-0000-000000000007")

_OCR_SPAN = "INVOICE 42"
_OBS_SPAN = "A red valve sits on the bench."
_DOCUMENT = (
    f"## Visible text (OCR)\n\n{_OCR_SPAN}\n\n## Visual description\n\n{_OBS_SPAN}\n"
)
_OCR_START = _DOCUMENT.find(_OCR_SPAN)
_OBS_START = _DOCUMENT.find(_OBS_SPAN)
_WHOLE_IMAGE = ImageRegionLocator(
    region=NormalizedRegion(x=0.0, y=0.0, w=1.0, h=1.0), precision="image"
)
_OCR_REGION = ImageRegionLocator(
    region=NormalizedRegion(x=0.1, y=0.1, w=0.4, h=0.2), precision="region"
)
_CONVERSION_URI = "doc/conversion.json"
_SOURCE_MAP_URI = "doc/source_map.json"
_MARKDOWN_URI = "doc/document.md"
_BLOCKS_URI = "doc/blocks.json"
_STALE_BLOCKS = b'{"blockizer_version": "stale", "blocks": []}'


class _MemoryStore:
    """In-memory artifact store that raises FileNotFoundError like the adapters."""

    def __init__(self, *, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        try:
            return self.objects[key.root]
        except KeyError as error:
            raise FileNotFoundError(key.root) from error


class _RecordingCatalog:
    """Captures extraction and reuse writes without a database."""

    def __init__(self) -> None:
        self.claims: tuple[ClaimRecord, ...] = ()
        self.decisions: tuple[DecisionRecord, ...] = ()
        self.occurrences: dict[UUID, OccurrenceProvenance] | None = None
        self.attached: tuple[UUID, UUID] | None = None
        self.reuse_occurrences: dict[UUID, OccurrenceProvenance] | None = None
        self.prior: UUID | None = None
        self.anchors: tuple[ReusedClaimAnchor, ...] = ()
        self.record_calls = 0

    def chunk_already_extracted(
        self, *, chunk_id: UUID, extractor_version: str
    ) -> bool:
        return False

    def prior_extracted_chunk(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        extraction_input_hash: str,
    ) -> UUID | None:
        return self.prior

    def claims_for_occurrence_reuse(
        self, *, chunk_id: UUID
    ) -> tuple[ReusedClaimAnchor, ...]:
        return self.anchors

    def attach_reused_claims(
        self,
        *,
        deployment_id: UUID,
        chunk_id: UUID,
        prior_chunk_id: UUID,
        occurrences: dict[UUID, OccurrenceProvenance] | None = None,
        evidence_spans: object = None,
    ) -> int:
        self.attached = (chunk_id, prior_chunk_id)
        self.reuse_occurrences = occurrences
        return 0 if not self.anchors else len(self.anchors)

    def copy_reused_decisions(self, *, chunk_id: UUID, prior_chunk_id: UUID) -> int:
        return 0

    def record_extraction(
        self,
        *,
        claims: tuple[ClaimRecord, ...],
        decisions: tuple[DecisionRecord, ...],
        occurrences: dict[UUID, OccurrenceProvenance] | None = None,
    ) -> None:
        self.record_calls += 1
        self.claims = claims
        self.decisions = decisions
        self.occurrences = occurrences


class _ChunkCatalogStub:
    """Returns a fixed source and the target chunk window."""

    def __init__(self, *, source: ChunkSource, chunk: ChunkForEmbedding) -> None:
        self.source = source
        self.chunk = chunk

    def chunk_source(self, *, representation_id: UUID) -> ChunkSource:
        return self.source

    def representation_id_for_chunk(self, *, chunk_id: UUID) -> UUID | None:
        return self.source.representation_id

    def chunks_for_extract(
        self, *, representation_id: UUID, chunker_version: str, chunk_id: UUID
    ) -> tuple[ChunkForEmbedding, ...]:
        return (self.chunk.model_copy(update={"chunk_id": chunk_id}),)


def _source(*, conversion_uri: str | None = _CONVERSION_URI) -> ChunkSource:
    return ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri=_MARKDOWN_URI,
        blocks_uri=_BLOCKS_URI,
        conversion_uri=conversion_uri,
        title="Scan",
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structurer",
        sections=(
            SectionSpan(
                section_id=_SECTION,
                node_path="0",
                role="body",
                block_start=0,
                block_end=0,
            ),
        ),
    )


def _chunk() -> ChunkForEmbedding:
    return ChunkForEmbedding(
        chunk_id=_CHUNK,
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=0,
        char_start=0,
        char_end=len(_DOCUMENT),
        chunk_content_hash="sha256:fixture",
        extraction_input_hash="sha256:fixture-in",
        section_role="body",
        section_path="0",
        section_id=_SECTION,
    )


def _work(*, chunk_id: UUID = _CHUNK) -> ClaimedWork:
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=_DEPLOYMENT,
        target_kind=ProcessingTarget.CHUNK,
        target_id=chunk_id,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version="e2-test",
        content_hash="hash",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={"representation_id": str(_REPR), "version_id": str(_VERSION)},
    )


def _manifest_bytes(*, sha256: str) -> bytes:
    payload = {
        "route": "image_ocr_description",
        "derivation_ranges": [
            DerivationRange(
                start=_OCR_START,
                end=_OCR_START + len(_OCR_SPAN),
                derivation_kind="ocr",
                evidence_mode="source_expression",
            ).model_dump(mode="json"),
            DerivationRange(
                start=_OBS_START,
                end=_OBS_START + len(_OBS_SPAN),
                derivation_kind="vlm_description",
                evidence_mode="model_observation",
            ).model_dump(mode="json"),
        ],
        "source_map": {"uri": _SOURCE_MAP_URI, "sha256": sha256, "entry_count": 2},
    }
    return json.dumps(payload).encode("utf-8")


def _source_map_bytes() -> bytes:
    payload = {
        "entries": [
            SourceMapEntry(
                start=_OCR_START,
                end=_OCR_START + len(_OCR_SPAN),
                locators=(_OCR_REGION,),
            ).model_dump(mode="json"),
            SourceMapEntry(
                start=_OBS_START,
                end=_OBS_START + len(_OBS_SPAN),
                locators=(_WHOLE_IMAGE,),
            ).model_dump(mode="json"),
        ]
    }
    return json.dumps(payload).encode("utf-8")


def _payloads(prompt: str, type_name: str) -> dict[str, object]:
    """Canned Selection/Claimify using engine-assigned origin-eligible labels."""
    if type_name == "SelectionResponse":
        return {
            "candidates": [
                {"source_span": _OCR_SPAN, "outcome": "keep"},
                {"source_span": _OBS_SPAN, "outcome": "keep"},
            ]
        }
    claims: list[dict[str, object]] = []
    for match in re.finditer(
        r"\[(S\d+)\] TARGET \(origin-eligible\):\n(.*?)(?=\n\[S|\Z)", prompt, flags=re.S
    ):
        label, body = match.group(1), match.group(2)
        if _OCR_SPAN in body:
            claims.append(
                {
                    "claim_text": "The invoice number is 42.",
                    "source_refs": [label],
                    "entailment_self_verdict": True,
                }
            )
        elif _OBS_SPAN in body:
            claims.append(
                {
                    "claim_text": "The image shows a red valve on a bench.",
                    "source_refs": [label],
                    "entailment_self_verdict": True,
                }
            )
    return {"claims": claims}


def _with_blocks(objects: dict[str, bytes]) -> dict[str, bytes]:
    """Ensure extract can load a (stale) blocks sidecar and re-blockize."""
    return {**objects, _BLOCKS_URI: _STALE_BLOCKS}


def _handler(
    *, catalog: _RecordingCatalog, store: _MemoryStore, source: ChunkSource
) -> ExtractClaimsHandler:
    chunk = _chunk()
    return ExtractClaimsHandler(
        catalog=cast("ClaimCatalog", catalog),
        chunk_catalog=cast(
            "ChunkCatalog", _ChunkCatalogStub(source=source, chunk=chunk)
        ),
        artifact_store=cast("ObjectStorePort", store),
        model_provider=FakeModelProvider(generate_router=_payloads),
        settings=E2Settings(),
        chunker_version="test-chunker",
    )


def test_fresh_extract_stamps_ocr_and_description_separately() -> None:
    """E2 loads the conversion manifest and attributes each grounded span."""
    map_bytes = _source_map_bytes()
    digest = hashlib.sha256(map_bytes).hexdigest()
    store = _MemoryStore(
        objects=_with_blocks(
            {
                _MARKDOWN_URI: _DOCUMENT.encode("utf-8"),
                _CONVERSION_URI: _manifest_bytes(sha256=digest),
                _SOURCE_MAP_URI: map_bytes,
            }
        )
    )
    catalog = _RecordingCatalog()
    handler = _handler(catalog=catalog, store=store, source=_source())
    handler.handle(work=_work(), meter=NoopCostMeter())
    assert catalog.record_calls == 1
    assert catalog.occurrences is not None
    by_span = {
        claim.source_span: catalog.occurrences[claim.claim_id]
        for claim in catalog.claims
    }
    assert set(by_span) == {_OCR_SPAN, _OBS_SPAN}
    ocr = by_span[_OCR_SPAN]
    assert ocr.derivation_kind == "ocr"
    assert ocr.evidence_mode == "source_expression"
    assert ocr.source_locators == (_OCR_REGION,)
    observation = by_span[_OBS_SPAN]
    assert observation.derivation_kind == "vlm_description"
    assert observation.evidence_mode == "model_observation"
    assert observation.source_locators == (_WHOLE_IMAGE,)
    for claim in catalog.claims:
        if claim.source_span == _OCR_SPAN:
            assert claim.char_start == _OCR_START
        else:
            assert claim.char_start == _OBS_START


def test_missing_conversion_manifest_does_not_persist_claims() -> None:
    """A referenced conversion.json that is absent must not publish claims."""
    catalog = _RecordingCatalog()
    store = _MemoryStore(
        objects=_with_blocks({_MARKDOWN_URI: _DOCUMENT.encode("utf-8")})
    )
    handler = _handler(catalog=catalog, store=store, source=_source())
    with pytest.raises(ProvenanceMetadataMissingError, match="conversion manifest"):
        handler.handle(work=_work(), meter=NoopCostMeter())
    assert catalog.record_calls == 0
    assert catalog.claims == ()


def test_corrupt_conversion_manifest_is_terminal_and_persists_nothing() -> None:
    """Corrupt JSON dead-letters; no misleading occurrence rows are written."""
    catalog = _RecordingCatalog()
    store = _MemoryStore(
        objects=_with_blocks(
            {_MARKDOWN_URI: _DOCUMENT.encode("utf-8"), _CONVERSION_URI: b"{not-json"}
        )
    )
    handler = _handler(catalog=catalog, store=store, source=_source())
    with pytest.raises(NonRetryableHandlerError, match="corrupt"):
        handler.handle(work=_work(), meter=NoopCostMeter())
    assert catalog.record_calls == 0


def test_missing_referenced_source_map_does_not_persist_claims() -> None:
    """A conversion.json pointer whose map is absent fails closed."""
    catalog = _RecordingCatalog()
    store = _MemoryStore(
        objects=_with_blocks(
            {
                _MARKDOWN_URI: _DOCUMENT.encode("utf-8"),
                _CONVERSION_URI: _manifest_bytes(sha256="deadbeef"),
            }
        )
    )
    handler = _handler(catalog=catalog, store=store, source=_source())
    with pytest.raises(ProvenanceMetadataMissingError, match="source map"):
        handler.handle(work=_work(), meter=NoopCostMeter())
    assert catalog.record_calls == 0


def test_reuse_resolves_against_target_chunk_not_prior_coordinates() -> None:
    """Prior offsets are ignored; the target document interval is stamped."""
    map_bytes = _source_map_bytes()
    digest = hashlib.sha256(map_bytes).hexdigest()
    store = _MemoryStore(
        objects=_with_blocks(
            {
                _MARKDOWN_URI: _DOCUMENT.encode("utf-8"),
                _CONVERSION_URI: _manifest_bytes(sha256=digest),
                _SOURCE_MAP_URI: map_bytes,
            }
        )
    )
    catalog = _RecordingCatalog()
    catalog.prior = _PRIOR
    prior_claim_id = uuid4()
    catalog.anchors = (
        ReusedClaimAnchor(
            claim_id=prior_claim_id,
            evidence_spans=(
                EvidenceSpan(
                    char_start=_OBS_START, char_end=_OBS_START + len(_OBS_SPAN)
                ),
            ),
        ),
    )
    handler = _handler(catalog=catalog, store=store, source=_source())
    handler.handle(work=_work(), meter=NoopCostMeter())
    assert catalog.record_calls == 0
    assert catalog.attached == (_CHUNK, _PRIOR)
    assert catalog.reuse_occurrences is not None
    resolved = catalog.reuse_occurrences[prior_claim_id]
    assert resolved.derivation_kind == "vlm_description"
    assert resolved.evidence_mode == "model_observation"
    assert resolved.source_locators == (_WHOLE_IMAGE,)
