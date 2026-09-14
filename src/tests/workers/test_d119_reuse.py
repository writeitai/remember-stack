"""Handler-level D119 reuse: same claim IDs, no extra extraction calls."""

from typing import cast
from uuid import UUID
from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core.chunker import extraction_input_hash
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimRecord
from rememberstack.model import DecisionRecord
from rememberstack.model import EvidenceSpan
from rememberstack.model import ModelRequest
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import SectionSpan
from rememberstack.model.occurrence_provenance import ReusedClaimAnchor
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.chunk_catalog import ChunkCatalog
from rememberstack.spine.claim_catalog import ClaimCatalog
from rememberstack.workers import E2Settings
from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION
from rememberstack.workers.e2 import ExtractClaimsHandler

_DEPLOYMENT = UUID("11910000-0000-0000-0000-000000000001")
_DOC = UUID("11910000-0000-0000-0000-000000000002")
_CHUNK = UUID("11910000-0000-0000-0000-000000000003")
_PRIOR = UUID("11910000-0000-0000-0000-000000000004")
_VERSION = UUID("11910000-0000-0000-0000-000000000005")
_REPR = UUID("11910000-0000-0000-0000-000000000006")
_SECTION = UUID("11910000-0000-0000-0000-000000000007")
_BODY = "Joanna's third screenplay explores loss, identity and connection."
_DOCUMENT = f"{_BODY}\n"
_MARKDOWN = "doc/document.md"
_BLOCKS = "doc/blocks.json"


class _FailIfCalled(FakeModelProvider):
    """A double that fails if extraction is invoked after reuse should have hit."""

    def generate(self, *, request: ModelRequest, response_type: type[object]):  # type: ignore[override]
        raise AssertionError("extraction must not be called on a reuse hit")


class _MemoryStore:
    def __init__(self, *, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def read_bytes(self, *, key: object) -> bytes:
        root = getattr(key, "root", key)
        try:
            return self.objects[str(root)]
        except KeyError as error:
            raise FileNotFoundError(str(root)) from error


class _RecordingCatalog:
    def __init__(self) -> None:
        self.prior: UUID | None = _PRIOR
        self.anchors: tuple[ReusedClaimAnchor, ...] = (
            ReusedClaimAnchor(
                claim_id=uuid4(),
                evidence_spans=(EvidenceSpan(char_start=0, char_end=len(_BODY)),),
            ),
        )
        self.attached = 0
        self.record_calls = 0
        self.reuse_spans: object = None

    def chunk_already_extracted(
        self, *, chunk_id: UUID, extractor_version: str
    ) -> bool:
        return False

    def prior_extracted_chunk(self, **kwargs: object) -> UUID | None:
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
        occurrences: object = None,
        evidence_spans: object = None,
    ) -> int:
        self.attached += 1
        self.reuse_spans = evidence_spans
        return len(self.anchors)

    def copy_reused_decisions(self, *, chunk_id: UUID, prior_chunk_id: UUID) -> int:
        return 0

    def record_extraction(
        self,
        *,
        claims: tuple[ClaimRecord, ...],
        decisions: tuple[DecisionRecord, ...],
        occurrences: object = None,
    ) -> None:
        self.record_calls += 1


class _ChunkCatalogStub:
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


def test_reuse_hit_does_not_call_extractor() -> None:
    """A matching extraction_input_hash remaps spans and never calls the model."""
    source = ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri=_MARKDOWN,
        blocks_uri=_BLOCKS,
        conversion_uri=None,
        title="Screenplay notes",
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
    chunk = ChunkForEmbedding(
        chunk_id=_CHUNK,
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=0,
        char_start=0,
        char_end=len(_DOCUMENT),
        chunk_content_hash="sha256:body",
        extraction_input_hash="sha256:same-inputs",
        section_role="body",
        section_path="0",
        section_id=_SECTION,
    )
    catalog = _RecordingCatalog()
    handler = ExtractClaimsHandler(
        catalog=cast("ClaimCatalog", catalog),
        chunk_catalog=cast(
            "ChunkCatalog", _ChunkCatalogStub(source=source, chunk=chunk)
        ),
        artifact_store=cast(
            "ObjectStorePort",
            _MemoryStore(
                objects={
                    _MARKDOWN: _DOCUMENT.encode("utf-8"),
                    _BLOCKS: b'{"blockizer_version": "stale", "blocks": []}',
                }
            ),
        ),
        model_provider=_FailIfCalled(),
        settings=E2Settings(),
        chunker_version="test-chunker",
    )
    handler.handle(
        work=ClaimedWork(
            processing_id=uuid4(),
            deployment_id=_DEPLOYMENT,
            target_kind=ProcessingTarget.CHUNK,
            target_id=_CHUNK,
            stage=PipelineStage.EXTRACT_CLAIMS,
            component_version=E2_EXTRACTOR_VERSION,
            content_hash="hash",
            lane=ProcessingLane.STEADY,
            attempt=1,
            payload={"representation_id": str(_REPR), "version_id": str(_VERSION)},
        ),
        meter=NoopCostMeter(),
    )
    assert catalog.record_calls == 0
    assert catalog.attached == 1
    spans = catalog.reuse_spans
    assert isinstance(spans, dict)
    remapped = next(iter(spans.values()))
    assert remapped[0].char_start == 0
    assert remapped[0].char_end == len(_BODY)


def test_header_timestamp_is_part_of_extraction_input_hash() -> None:
    """Existing conservative policy: a header timestamp change invalidates reuse."""
    shared = {
        "own_block_hashes": ("sha256:body",),
        "neighbor_block_hashes": ("", ""),
        "extractor_version": E2_EXTRACTOR_VERSION,
        "structurer_version": "struct",
    }
    first = extraction_input_hash(
        header_facts=("title", "upload", "2024-01-01T00:00:00+00:00", "", "en"),
        **shared,
    )
    second = extraction_input_hash(
        header_facts=("title", "upload", "2024-01-02T00:00:00+00:00", "", "en"),
        **shared,
    )
    assert first != second


def test_previous_only_and_next_only_neighbor_hashes_differ() -> None:
    """Same neighbour text on opposite sides is not the same extraction input."""
    shared = {
        "own_block_hashes": ("sha256:target",),
        "extractor_version": E2_EXTRACTOR_VERSION,
        "structurer_version": "struct",
        "header_facts": ("title", "upload", "", "", "en"),
    }
    previous_only = extraction_input_hash(
        neighbor_block_hashes=("sha256:X", ""), **shared
    )
    next_only = extraction_input_hash(neighbor_block_hashes=("", "sha256:X"), **shared)
    both_absent = extraction_input_hash(neighbor_block_hashes=("", ""), **shared)
    assert previous_only != next_only
    assert previous_only != both_absent
    assert next_only != both_absent
