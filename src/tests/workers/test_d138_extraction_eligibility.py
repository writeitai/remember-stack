"""D138 §1/§2 with D133 §4.5: search-only text is chunked but never extracted.

Postgres-free proofs of the whole path: the policy labels blocks, convert
rejects a block that mixes eligible and ineligible ranges, E1 cuts chunks
where eligibility changes and keys ineligible chunks apart, E2 completes an
ineligible chunk without a Selection call, and structure makes no model call
for a representation without prose. Convert also routes an oversized file to
the card and hands file hints to routes that read them.
"""

import json
from pathlib import Path
from typing import cast
from typing import TYPE_CHECKING
from uuid import UUID
from uuid import uuid4

import pytest

from rememberstack.adapters.converters.card import CardConverter
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core import ConversionRouter
from rememberstack.core import Converter
from rememberstack.core import pack_blocks
from rememberstack.core.extraction_eligibility import block_eligibility
from rememberstack.core.extraction_eligibility import (
    EXTRACTION_ELIGIBILITY_POLICY_VERSION,
)
from rememberstack.core.extraction_eligibility import is_model_free
from rememberstack.core.extraction_eligibility import MixedEligibilityError
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import ConvertSource
from rememberstack.model import DecisionType
from rememberstack.model import DerivationRange
from rememberstack.model import FileHints
from rememberstack.model import ManifestComponent
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import RepresentationRecord
from rememberstack.model import SectionSpan
from rememberstack.model import SelectionDropReason
from rememberstack.model import StructureRouteTag
from rememberstack.model import StructureSource
from rememberstack.workers import E2Settings
from rememberstack.workers import StructureHandler
from rememberstack.workers.e0 import ConvertHandler
from rememberstack.workers.e0 import E0_RULE_ROLE_VERSION
from rememberstack.workers.e1 import _chunk_record
from rememberstack.workers.e1 import ChunkHandler
from rememberstack.workers.e2 import ExtractClaimsHandler
from tests.workers.e2_test_doubles import SingleChunkCatalog
from tests.workers.e2_test_doubles import with_ground_claims
from tests.workers.test_claimify_loss_ledger import _chunk
from tests.workers.test_claimify_loss_ledger import _DOC_MD
from tests.workers.test_claimify_loss_ledger import _MemoryStore
from tests.workers.test_claimify_loss_ledger import _RecordingCatalog
from tests.workers.test_claimify_loss_ledger import _source
from tests.workers.test_d79_structure_route import _Catalog as _StructureCatalog
from tests.workers.test_d79_structure_route import _work as _structure_work

if TYPE_CHECKING:
    from rememberstack.ports.object_store import ObjectStorePort
    from rememberstack.spine.chunk_catalog import ChunkCatalog
    from rememberstack.spine.claim_catalog import ClaimCatalog
    from rememberstack.spine.document_catalog import DocumentCatalog

_DEPLOYMENT = UUID("d1380000-0000-0000-0000-000000000001")
_DOC = UUID("d1380000-0000-0000-0000-000000000002")
_VERSION = UUID("d1380000-0000-0000-0000-000000000003")
_REPR = UUID("d1380000-0000-0000-0000-000000000004")
_SECTION = UUID("d1380000-0000-0000-0000-000000000005")

_PROSE = "The release moved to March after the audit.\n\n"
_CODE = "def release():\n    return 'March'\n\nprint(release())\n"
_NOTEBOOK_MD = _PROSE + _CODE + "\nThe audit closed in April.\n"


def _ranges(*spans: tuple[int, int, str]) -> tuple[DerivationRange, ...]:
    """Labelled ranges from (start, end, kind) triples."""
    return tuple(
        DerivationRange(
            start=start,
            end=end,
            derivation_kind=kind,
            evidence_mode="source_expression",
        )
        for start, end, kind in spans
    )


def _notebook_ranges() -> tuple[DerivationRange, ...]:
    """Prose, then code, then prose again, each on block boundaries."""
    code_end = len(_PROSE) + len(_CODE)
    return _ranges(
        (0, len(_PROSE), "passthrough"),
        (len(_PROSE), code_end, "code"),
        (code_end, len(_NOTEBOOK_MD), "passthrough"),
    )


def test_block_eligibility_follows_the_policy() -> None:
    """Ineligible kinds mark their blocks; unlabelled blocks stay eligible."""
    blocks = blockize(document_md=_NOTEBOOK_MD)
    assert block_eligibility(blocks=blocks, ranges=_notebook_ranges()) == (
        True,
        False,
        False,
        True,
    )
    assert block_eligibility(blocks=blocks, ranges=()) == (True,) * len(blocks)


def test_a_block_mixing_eligible_and_ineligible_ranges_is_refused() -> None:
    """Eligibility may only change at a block boundary (D133 §4.5)."""
    blocks = blockize(document_md="prose and code in one paragraph\n")
    with pytest.raises(MixedEligibilityError, match="block 0"):
        block_eligibility(
            blocks=blocks, ranges=_ranges((0, 5, "passthrough"), (5, 31, "code"))
        )


def test_model_free_means_every_range_is_ineligible() -> None:
    """A card or code file has no prose; an empty or mixed reading does."""
    assert is_model_free(ranges=_ranges((0, 10, "file_card")))
    assert not is_model_free(ranges=_notebook_ranges())
    assert not is_model_free(ranges=())


def test_the_chunker_cuts_where_eligibility_changes() -> None:
    """A generous budget still yields three chunks: prose, code, prose."""
    blocks = blockize(document_md=_NOTEBOOK_MD)
    chunks = pack_blocks(
        blocks=blocks,
        sections=(
            SectionSpan(
                section_id=_SECTION,
                node_path="0",
                role="body",
                block_start=0,
                block_end=len(blocks) - 1,
            ),
        ),
        document_md=_NOTEBOOK_MD,
        params=ChunkerParams(token_budget=10_000),
        ineligible_ordinals=frozenset({1, 2}),
    )
    assert [(c.block_start, c.block_end, c.extraction_eligible) for c in chunks] == [
        (0, 0, True),
        (1, 2, False),
        (3, 3, True),
    ]


class _ChunkCatalog:
    """Chunk stage surface: one source, no prior chunks, captured records."""

    def __init__(self, *, source: ChunkSource) -> None:
        self.source = source
        self.records: tuple[ChunkRecord, ...] = ()

    def chunk_source(self, *, representation_id: UUID) -> ChunkSource:
        assert representation_id == _REPR
        return self.source

    def existing_chunk_ids(
        self, *, representation_id: UUID, chunker_version: str
    ) -> tuple[UUID, ...]:
        del representation_id, chunker_version
        return ()

    def record_chunks(self, *, records: tuple[ChunkRecord, ...]) -> None:
        self.records = records


def _write_representation(
    *, store: LocalFSObjectStore, document_md: str, ranges: tuple[DerivationRange, ...]
) -> None:
    """The three artifacts E1 and structure read: markdown, blocks, manifest."""
    blocks = blockize(document_md=document_md)
    store.write_bytes(key=ObjectKey("doc/document.md"), content=document_md.encode())
    store.write_bytes(
        key=ObjectKey("doc/blocks.json"),
        content=json.dumps(
            {
                "blockizer_version": BLOCKIZER_VERSION,
                "markdown_chars": len(document_md),
                "blocks": [block.model_dump(mode="json") for block in blocks],
            }
        ).encode(),
    )
    store.write_bytes(
        key=ObjectKey("doc/conversion.json"),
        content=json.dumps(
            {
                "derivation_ranges": [
                    labeled.model_dump(mode="json") for labeled in ranges
                ]
            }
        ).encode(),
    )


def test_e1_records_eligibility_and_keys_ineligible_chunks_apart(
    tmp_path: Path,
) -> None:
    """Chunk rows carry the flag and policy version; ineligible keys differ."""
    store = LocalFSObjectStore(root=tmp_path)
    _write_representation(
        store=store, document_md=_NOTEBOOK_MD, ranges=_notebook_ranges()
    )
    block_count = len(blockize(document_md=_NOTEBOOK_MD))
    source = ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri="doc/document.md",
        blocks_uri="doc/blocks.json",
        conversion_uri="doc/conversion.json",
        title="notebook",
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
                block_end=block_count - 1,
            ),
        ),
    )
    catalog = _ChunkCatalog(source=source)
    params = ChunkerParams(token_budget=10_000)
    ChunkHandler(
        catalog=cast("ChunkCatalog", catalog), artifact_store=store, params=params
    ).handle(work=_work(stage=PipelineStage.CHUNK), meter=NoopCostMeter())

    assert [record.extraction_eligible for record in catalog.records] == [
        True,
        False,
        True,
    ]
    assert {record.extraction_eligibility_version for record in catalog.records} == {
        EXTRACTION_ELIGIBILITY_POLICY_VERSION
    }
    packed = pack_blocks(
        blocks=blockize(document_md=_NOTEBOOK_MD),
        sections=source.sections,
        document_md=_NOTEBOOK_MD,
        params=params,
        ineligible_ordinals=frozenset({1, 2}),
    )
    as_if_eligible = _chunk_record(
        source=source,
        packed=tuple(
            chunk.model_copy(update={"extraction_eligible": True}) for chunk in packed
        ),
        index=1,
        chunker_version=chunker_version(params=params),
    )
    # the policy version joins only the ineligible chunk's reuse key
    assert catalog.records[1].extraction_input_hash != (
        as_if_eligible.extraction_input_hash
    )
    unchanged = _chunk_record(
        source=source,
        packed=packed,
        index=0,
        chunker_version=chunker_version(params=params),
    )
    assert catalog.records[0].extraction_input_hash == unchanged.extraction_input_hash


def test_e2_completes_an_ineligible_chunk_without_a_model_call() -> None:
    """Selection freezes an empty result; Claimify records the empty marker."""
    recorder = _RecordingCatalog()
    source = _source()
    chunk = _chunk().model_copy(update={"extraction_eligible": False})
    handler = ExtractClaimsHandler(
        catalog=cast("ClaimCatalog", recorder),
        chunk_catalog=cast(
            "ChunkCatalog", SingleChunkCatalog(source=source, chunk=chunk)
        ),
        artifact_store=cast(
            "ObjectStorePort",
            _MemoryStore(
                objects={
                    source.markdown_uri: _DOC_MD.encode("utf-8"),
                    source.blocks_uri: b'{"blockizer_version": "stale", "blocks": []}',
                }
            ),
        ),
        # no canned payloads: any model call fails the test
        model_provider=FakeModelProvider(),
        settings=E2Settings(),
        chunker_version="test-chunker",
    )
    work = ClaimedWork(
        processing_id=chunk.chunk_id,
        deployment_id=source.deployment_id,
        target_kind=ProcessingTarget.CHUNK,
        target_id=chunk.chunk_id,
        stage=PipelineStage.EXTRACT_CLAIMS,
        component_version="e2-test",
        content_hash="hash",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "representation_id": str(source.representation_id),
            "version_id": str(source.version_id),
            "chunk_id": str(chunk.chunk_id),
        },
    )
    handler.handle(work=work, meter=NoopCostMeter())
    frozen = recorder.selections.load(chunk_id=chunk.chunk_id, extractor_version="x")
    assert frozen is not None
    assert frozen.selection.candidates == ()
    assert frozen.cards == ()
    assert frozen.input_hash == chunk.extraction_input_hash

    handler.handle(work=with_ground_claims(work=work), meter=NoopCostMeter())
    assert recorder.claims == ()
    # the terminal no_info marker, exactly like an empty Selection result
    assert [
        (decision.decision_type, decision.reason) for decision in recorder.decisions
    ] == [(DecisionType.SELECTION_DROP, SelectionDropReason.NO_INFO)]


class _ModelFreeStructureCatalog(_StructureCatalog):
    """The D79 routing catalog, pointing at a stored converter manifest."""

    def structure_source(self, *, representation_id: UUID) -> StructureSource:
        return (
            super()
            .structure_source(representation_id=representation_id)
            .model_copy(update={"conversion_uri": "doc/conversion.json"})
        )


class _NoModel:
    """A provider that fails the test on any call."""

    def generate(self, *, request: object, response_type: object) -> object:
        raise AssertionError(f"unexpected model call for {response_type}")

    def embed(self, *, request: object) -> object:
        raise AssertionError("unexpected embedding call")


def _structure(
    *, tmp_path: Path, document_md: str, kind: str, provider: object
) -> _ModelFreeStructureCatalog:
    """Run the structure stage over one labelled representation."""
    store = LocalFSObjectStore(root=tmp_path)
    _write_representation(
        store=store,
        document_md=document_md,
        ranges=_ranges((0, len(document_md), kind)),
    )
    catalog = _ModelFreeStructureCatalog()
    StructureHandler(
        catalog=cast("DocumentCatalog", catalog),
        artifact_store=store,
        model_provider=provider,  # type: ignore[arg-type]
    ).handle(work=_structure_work(), meter=NoopCostMeter())
    return catalog


_SECTIONED = "\n\n".join(
    ("# Setup", "a" * 80, "# References", "b" * 80, "# Usage", "c" * 80, "# Notes", "d")
)


def test_structure_makes_no_model_call_without_prose(tmp_path: Path) -> None:
    """Search-only text keeps its parsed headings, rule roles and no summaries."""
    catalog = _structure(
        tmp_path=tmp_path, document_md=_SECTIONED, kind="code", provider=_NoModel()
    )
    assert catalog.checks == []
    (generation,) = catalog.generations
    assert generation.route_tag is StructureRouteTag.PARSER
    assert generation.skeleton_check_version is None
    assert generation.summary_version is None
    assert generation.placement_path is None
    assert generation.roles_version == E0_RULE_ROLE_VERSION
    roles = {section.title: section.role for section in generation.sections}
    assert roles["References"] == "references"
    assert roles["Setup"] == "body"
    assert all(section.summary is None for section in generation.sections)


def test_structure_still_calls_models_for_prose(tmp_path: Path) -> None:
    """The control: the same text labelled prose reaches the D79 model seats."""
    provider = FakeModelProvider(
        generate_payloads={
            "SkeletonCheckResponse": {"verdict": "coherent"},
            "RoleClassificationResponse": {"assignments": []},
            "SectionSummaryResponse": {"summary": "A section."},
            "RootSummaryPlacementResponse": {
                "summary": "A document.",
                "placement_path": "/proof/",
            },
        }
    )
    _structure(
        tmp_path=tmp_path, document_md=_SECTIONED, kind="passthrough", provider=provider
    )
    assert provider.generated_requests


class _ConvertCatalog:
    """Convert stage surface: one version, captured failure and representation."""

    def __init__(self, *, mime: str, byte_size: int) -> None:
        self.source = ConvertSource(
            deployment_id=_DEPLOYMENT,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="hash",
            mime=mime,
            byte_size=byte_size,
            raw_uri="raw/original",
            title="upload",
            file_name="scan.pdf",
            source_path="archive/2025",
        )
        self.failed: str | None = None
        self.recorded: RepresentationRecord | None = None

    def convert_source(self, *, version_id: UUID) -> ConvertSource:
        assert version_id == _VERSION
        return self.source

    def existing_representation(self, **_: object) -> UUID | None:
        return None

    def mark_version_failed(self, *, version_id: UUID, error: str) -> None:
        del version_id
        self.failed = error

    def record_representation(
        self, *, record: RepresentationRecord, metadata: object = None
    ) -> None:
        del metadata
        self.recorded = record


class _MixedConverter:
    """A buggy route labelling one paragraph as both prose and code."""

    name = "mixed"
    version = "mixed-1"

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        del content, mime
        text = "prose then code in one block\n"
        return ConversionResult(
            document_md=text,
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="mixed", version="1", execution="library-local"
                    ),
                ),
                coverage=ConversionCoverage(policy="full", complete=True),
                derivation_ranges=_ranges(
                    (0, 5, "passthrough"), (5, len(text), "code")
                ),
            ),
        )


def _convert(
    *, tmp_path: Path, catalog: _ConvertCatalog, routes: dict[str, Converter]
) -> LocalFSObjectStore:
    """Run the convert stage once over stored raw bytes."""
    raw = LocalFSObjectStore(root=tmp_path / "raw")
    raw.write_bytes(key=ObjectKey("raw/original"), content=b"%PDF-1.7 tiny")
    artifacts = LocalFSObjectStore(root=tmp_path / "artifacts")
    ConvertHandler(
        catalog=cast("DocumentCatalog", catalog),
        raw_store=raw,
        artifact_store=artifacts,
        router=ConversionRouter(routes=routes),
    ).handle(work=_work(stage=PipelineStage.CONVERT), meter=NoopCostMeter())
    return artifacts


def test_convert_refuses_a_block_that_mixes_eligibility(tmp_path: Path) -> None:
    """A mis-labelled reading is a converter error, never mis-chunked silently."""
    catalog = _ConvertCatalog(mime="application/x-mixed", byte_size=10)
    with pytest.raises(NonRetryableHandlerError, match="mixes eligible"):
        _convert(
            tmp_path=tmp_path,
            catalog=catalog,
            routes={"application/x-mixed": _MixedConverter()},
        )
    assert catalog.failed is not None and "invalid envelope" in catalog.failed
    assert catalog.recorded is None


def test_convert_routes_an_oversized_file_to_the_card_with_hints(
    tmp_path: Path,
) -> None:
    """Over the family limit, even an unrouted PDF is carded, named by its hints."""
    catalog = _ConvertCatalog(mime="application/pdf", byte_size=100_000_001)
    artifacts = _convert(
        tmp_path=tmp_path,
        catalog=catalog,
        routes={"application/octet-stream": CardConverter()},
    )
    assert catalog.recorded is not None
    assert catalog.recorded.route == "card"
    card = artifacts.read_bytes(key=ObjectKey(catalog.recorded.markdown_uri)).decode()
    assert card.startswith("# scan.pdf\n")
    assert "- Source path: archive/2025" in card
    assert "- Family: pdf" in card


def test_file_hints_reach_only_routes_that_accept_them() -> None:
    """The card receives the version's name and path; FileHints stays optional."""
    result = CardConverter().convert(
        content=b"x",
        mime="application/octet-stream",
        hints=FileHints(file_name=None, source_path=None),
    )
    assert result.document_md.startswith("# Unnamed file\n")


def _work(*, stage: PipelineStage) -> ClaimedWork:
    """One version-grain claimed job for the convert or chunk stage."""
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=_DEPLOYMENT,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=_VERSION,
        stage=stage,
        component_version="test",
        content_hash="hash",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={"version_id": str(_VERSION), "representation_id": str(_REPR)},
    )
