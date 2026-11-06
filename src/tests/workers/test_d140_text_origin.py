"""D140 §5 text origin time, without PostgreSQL.

Proves the pure pieces and their wiring: the date-free reuse identity, the
eligibility rule an origin is inherited under, the single-date D56 key, the
chunk stage's lookup and replay, and the E2 header date and ``asserted_at``.
The lookup's SQL and the end-to-end reuse live in ``test_d140_text_origin_pg``.
"""

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import cast
from typing import TYPE_CHECKING
from uuid import UUID
from uuid import uuid4

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import ChunkerParams
from rememberstack.core.source_passages import build_passage_catalog
from rememberstack.core.text_origin import earliest_eligible_match
from rememberstack.core.text_origin import resolve_text_origin
from rememberstack.core.text_origin import reuse_identity_hash
from rememberstack.model import AddedContext
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkRecord
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimRecord
from rememberstack.model import ObjectKey
from rememberstack.model import PackedChunk
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import SectionSpan
from rememberstack.model import TextOriginMatch
from rememberstack.workers.e1 import _chunk_record
from rememberstack.workers.e1 import _reuse_identity_hash
from rememberstack.workers.e1 import ChunkHandler
from rememberstack.workers.e2 import _chunk_date
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import _header_text

if TYPE_CHECKING:
    from rememberstack.spine.chunk_catalog import ChunkCatalog

_DEPLOYMENT = UUID("d1400000-0000-0000-0000-000000000001")
_DOC = UUID("d1400000-0000-0000-0000-000000000002")
_VERSION = UUID("d1400000-0000-0000-0000-000000000003")
_REPR = UUID("d1400000-0000-0000-0000-000000000004")
_SECTION = UUID("d1400000-0000-0000-0000-000000000005")
_OTHER_SECTION = UUID("d1400000-0000-0000-0000-000000000006")

_JAN_2024 = datetime(2024, 1, 10, tzinfo=UTC)
_DEC_2023 = datetime(2023, 12, 10, tzinfo=UTC)
_NOV_2025 = datetime(2025, 11, 10, tzinfo=UTC)
_MAR_2026 = datetime(2026, 3, 1, tzinfo=UTC)

_DOC_MD = "The daily allowance is 50 EUR.\n\nApprovals follow the expense policy.\n"


def _source(
    *,
    source_modified_at: datetime | None = None,
    published_at: datetime | None = None,
    title: str | None = "Travel Policy",
    file_name: str | None = "travel.md",
    language: str | None = "en",
    source_kind: str = "intranet",
    structurer_version: str = "test-structurer",
    blockizer_version: str | None = BLOCKIZER_VERSION,
    sections: tuple[SectionSpan, ...] = (),
) -> ChunkSource:
    return ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPR,
        markdown_uri="doc/document.md",
        blocks_uri="doc/blocks.json",
        title=None,
        file_name=file_name,
        version_title=title,
        source_kind=source_kind,
        source_modified_at=source_modified_at,
        published_at=published_at,
        language=language,
        structurer_version=structurer_version,
        blockizer_version=blockizer_version,
        sections=sections,
    )


def _packed(
    *, hashes: tuple[str, ...], sections: tuple[UUID, ...] | None = None
) -> tuple[PackedChunk, ...]:
    owners = sections or tuple(_SECTION for _ in hashes)
    return tuple(
        PackedChunk(
            ordinal=ordinal,
            section_id=owner,
            block_start=ordinal,
            block_end=ordinal,
            char_start=ordinal * 10,
            char_end=ordinal * 10 + 9,
            chunk_content_hash=content_hash,
            token_count=3,
        )
        for ordinal, (content_hash, owner) in enumerate(
            zip(hashes, owners, strict=True)
        )
    )


def _identity(*, source: ChunkSource, packed: tuple[PackedChunk, ...]) -> str:
    return _reuse_identity_hash(source=source, packed=packed, index=1)


def _key(
    *,
    source: ChunkSource,
    text_origin_at: datetime | None,
    packed: tuple[PackedChunk, ...] | None = None,
) -> ChunkRecord:
    chunks = packed or _packed(hashes=("a", "b", "c"))
    return _chunk_record(
        source=source,
        packed=chunks,
        index=1,
        chunker_version="test-chunker",
        reuse_identity_hash=_identity(source=source, packed=chunks),
        text_origin_at=text_origin_at,
    )


def _match(*, at: datetime, version_no: int, ordinal: int) -> TextOriginMatch:
    return TextOriginMatch(
        reuse_identity_hash="identity",
        text_origin_at=at,
        version_no=version_no,
        ordinal=ordinal,
    )


# -- the reuse identity -------------------------------------------------------


def test_reuse_identity_ignores_every_date() -> None:
    """A newly dated version of unchanged text keeps its identity."""
    packed = _packed(hashes=("a", "b", "c"))
    undated = _identity(source=_source(), packed=packed)
    assert undated == _identity(
        source=_source(source_modified_at=_NOV_2025), packed=packed
    )
    assert undated == _identity(source=_source(published_at=_JAN_2024), packed=packed)


def test_reuse_identity_covers_text_neighbours_and_undated_header_facts() -> None:
    """Own text, same-section neighbours and every non-date header fact count."""
    packed = _packed(hashes=("a", "b", "c"))
    base = _identity(source=_source(), packed=packed)
    changed = (
        _identity(source=_source(), packed=_packed(hashes=("a", "B", "c"))),
        _identity(source=_source(), packed=_packed(hashes=("A", "b", "c"))),
        _identity(source=_source(), packed=_packed(hashes=("a", "b", "C"))),
        _identity(source=_source(title="Travel Rules"), packed=packed),
        _identity(source=_source(file_name="travel-2.md"), packed=packed),
        _identity(source=_source(source_kind="upload"), packed=packed),
        _identity(source=_source(language="cs"), packed=packed),
        _identity(
            source=_source(),
            packed=tuple(
                chunk.model_copy(update={"extraction_eligible": False})
                if chunk.ordinal == 1
                else chunk
                for chunk in packed
            ),
        ),
    )
    assert all(identity != base for identity in changed)
    assert len(set(changed)) == len(changed)


def test_reuse_identity_neighbours_follow_the_same_section_rule() -> None:
    """A neighbour in another section is not a neighbour, as in the D56 key."""
    split = (_SECTION, _SECTION, _OTHER_SECTION)
    source = _source()
    assert _identity(
        source=source, packed=_packed(hashes=("a", "b", "c"), sections=split)
    ) == _identity(
        source=source, packed=_packed(hashes=("a", "b", "other"), sections=split)
    )


def test_toolchain_bump_is_a_reuse_boundary() -> None:
    """Blockizer, structurer and extractor versions are all part of the identity."""
    arguments = {
        "own_block_hashes": ("b",),
        "neighbor_block_hashes": ("a", "c"),
        "header_facts": ("Travel Policy", "intranet", "en", "travel.md"),
        "blockizer_version": "blockizer-1",
        "structurer_version": "structurer-1",
        "extractor_version": "extractor-1",
    }
    base = reuse_identity_hash(**arguments)
    assert base == reuse_identity_hash(**arguments)
    for field in ("blockizer_version", "structurer_version", "extractor_version"):
        assert base != reuse_identity_hash(**{**arguments, field: "bumped"})
    packed = _packed(hashes=("a", "b", "c"))
    assert _identity(source=_source(), packed=packed) != _identity(
        source=_source(blockizer_version="blockizer-next"), packed=packed
    )
    assert _identity(source=_source(), packed=packed) != _identity(
        source=_source(structurer_version="structurer-next"), packed=packed
    )


# -- the eligibility rule -----------------------------------------------------


def test_no_match_takes_the_version_date() -> None:
    assert resolve_text_origin(matches=(), version_date=_NOV_2025) == _NOV_2025


def test_unknown_version_date_has_no_eligible_match() -> None:
    """An undated version keeps today's "date unknown", even with matches."""
    matches = (_match(at=_DEC_2023, version_no=1, ordinal=0),)
    assert earliest_eligible_match(matches=matches, version_date=None) is None
    assert resolve_text_origin(matches=matches, version_date=None) is None


def test_earliest_eligible_origin_is_inherited() -> None:
    matches = (
        _match(at=_JAN_2024, version_no=2, ordinal=0),
        _match(at=_DEC_2023, version_no=1, ordinal=4),
    )
    assert resolve_text_origin(matches=matches, version_date=_NOV_2025) == _DEC_2023


def test_origin_equal_to_the_version_date_is_eligible() -> None:
    matches = (_match(at=_NOV_2025, version_no=1, ordinal=0),)
    assert resolve_text_origin(matches=matches, version_date=_NOV_2025) == _NOV_2025


def test_back_filled_older_edition_never_inherits_a_later_date() -> None:
    """A 2024 edition ingested after a 2026 one takes 2024 and extracts afresh."""
    matches = (_match(at=_MAR_2026, version_no=1, ordinal=0),)
    assert earliest_eligible_match(matches=matches, version_date=_JAN_2024) is None
    assert resolve_text_origin(matches=matches, version_date=_JAN_2024) == _JAN_2024
    source = _source(source_modified_at=_JAN_2024)
    assert (
        _key(source=source, text_origin_at=_JAN_2024).extraction_input_hash
        != _key(source=source, text_origin_at=_MAR_2026).extraction_input_hash
    )


def test_ties_break_by_version_number_then_ordinal() -> None:
    first = _match(at=_DEC_2023, version_no=1, ordinal=7)
    same_version_earlier_ordinal = _match(at=_DEC_2023, version_no=1, ordinal=2)
    later_version = _match(at=_DEC_2023, version_no=3, ordinal=0)
    assert (
        earliest_eligible_match(
            matches=(later_version, first, same_version_earlier_ordinal),
            version_date=_NOV_2025,
        )
        == same_version_earlier_ordinal
    )
    assert (
        earliest_eligible_match(matches=(later_version, first), version_date=_NOV_2025)
        == first
    )


# -- the D56 key --------------------------------------------------------------


def test_extraction_key_carries_the_text_origin_not_the_version_dates() -> None:
    """An inherited origin reproduces the earlier chunk's key exactly."""
    first = _key(source=_source(source_modified_at=_DEC_2023), text_origin_at=_DEC_2023)
    later = _key(
        source=_source(source_modified_at=_NOV_2025, published_at=_MAR_2026),
        text_origin_at=_DEC_2023,
    )
    fresh = _key(source=_source(source_modified_at=_NOV_2025), text_origin_at=_NOV_2025)
    assert first.extraction_input_hash == later.extraction_input_hash
    assert first.reuse_identity_hash == later.reuse_identity_hash
    assert fresh.extraction_input_hash != first.extraction_input_hash
    assert later.text_origin_at == _DEC_2023


# -- the chunk stage ----------------------------------------------------------


class _ChunkCatalog:
    """Chunk stage surface: one source, recorded lookups and rows."""

    def __init__(
        self,
        *,
        source: ChunkSource,
        matches: dict[str, TextOriginMatch] | None = None,
        existing: tuple[UUID, ...] = (),
    ) -> None:
        self.source = source
        self.matches = matches or {}
        self.existing = existing
        self.lookups: list[dict[str, object]] = []
        self.records: tuple[ChunkRecord, ...] = ()

    def chunk_source(self, *, representation_id: UUID) -> ChunkSource:
        assert representation_id == _REPR
        return self.source

    def existing_chunk_ids(
        self, *, representation_id: UUID, chunker_version: str
    ) -> tuple[UUID, ...]:
        del representation_id, chunker_version
        return self.existing

    def text_origin_matches(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        reuse_identity_hashes: tuple[str, ...],
        not_after: datetime,
    ) -> dict[str, TextOriginMatch]:
        self.lookups.append(
            {
                "deployment_id": deployment_id,
                "doc_id": doc_id,
                "hashes": reuse_identity_hashes,
                "not_after": not_after,
            }
        )
        return {
            identity: match
            for identity, match in self.matches.items()
            if identity in reuse_identity_hashes
        }

    def record_chunks(self, *, records: tuple[ChunkRecord, ...]) -> None:
        self.records = records


def _write_representation(*, store: LocalFSObjectStore) -> int:
    blocks = blockize(document_md=_DOC_MD)
    store.write_bytes(
        key=ObjectKey("doc/document.md"), content=_DOC_MD.encode(), storage_class="hot"
    )
    store.write_bytes(
        key=ObjectKey("doc/blocks.json"),
        content=json.dumps(
            {
                "blockizer_version": BLOCKIZER_VERSION,
                "markdown_chars": len(_DOC_MD),
                "blocks": [block.model_dump(mode="json") for block in blocks],
            }
        ).encode(),
        storage_class="hot",
    )
    return len(blocks)


def _sections(*, block_count: int) -> tuple[SectionSpan, ...]:
    return (
        SectionSpan(
            section_id=_SECTION,
            node_path="0",
            role="body",
            block_start=0,
            block_end=block_count - 1,
        ),
    )


def _work() -> ClaimedWork:
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=_DEPLOYMENT,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=_VERSION,
        stage=PipelineStage.CHUNK,
        component_version="test",
        content_hash="hash",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={"version_id": str(_VERSION), "representation_id": str(_REPR)},
    )


def _run_chunk_stage(*, catalog: _ChunkCatalog, store: LocalFSObjectStore) -> None:
    ChunkHandler(
        catalog=cast("ChunkCatalog", catalog),
        artifact_store=store,
        params=ChunkerParams(token_budget=6, anchor_modulus=1_000_000),
    ).handle(work=_work(), meter=NoopCostMeter())


def test_chunk_stage_inherits_a_match_and_dates_the_rest(tmp_path: Path) -> None:
    """Matched chunks take the earlier origin; the others take the version date."""
    store = LocalFSObjectStore(root=tmp_path)
    block_count = _write_representation(store=store)
    source = _source(
        source_modified_at=_NOV_2025,
        published_at=_MAR_2026,
        sections=_sections(block_count=block_count),
    )
    probe = _ChunkCatalog(source=source)
    _run_chunk_stage(catalog=probe, store=store)
    assert len(probe.records) == 2
    matched = probe.records[0].reuse_identity_hash
    catalog = _ChunkCatalog(
        source=source,
        matches={
            matched: TextOriginMatch(
                reuse_identity_hash=matched,
                text_origin_at=_DEC_2023,
                version_no=1,
                ordinal=0,
            )
        },
    )
    _run_chunk_stage(catalog=catalog, store=store)

    # the version date is source_modified_at first, then published_at
    assert catalog.lookups == [
        {
            "deployment_id": _DEPLOYMENT,
            "doc_id": _DOC,
            "hashes": tuple(record.reuse_identity_hash for record in probe.records),
            "not_after": _NOV_2025,
        }
    ]
    assert [record.text_origin_at for record in catalog.records] == [
        _DEC_2023,
        _NOV_2025,
    ]
    assert [record.reuse_identity_hash for record in catalog.records] == [
        record.reuse_identity_hash for record in probe.records
    ]
    assert catalog.records[0].extraction_input_hash != (
        probe.records[0].extraction_input_hash
    )
    assert catalog.records[1].extraction_input_hash == (
        probe.records[1].extraction_input_hash
    )


def test_chunk_stage_uses_published_at_when_no_modification_time(
    tmp_path: Path,
) -> None:
    store = LocalFSObjectStore(root=tmp_path)
    block_count = _write_representation(store=store)
    catalog = _ChunkCatalog(
        source=_source(
            published_at=_JAN_2024, sections=_sections(block_count=block_count)
        )
    )
    _run_chunk_stage(catalog=catalog, store=store)
    assert [lookup["not_after"] for lookup in catalog.lookups] == [_JAN_2024]
    assert {record.text_origin_at for record in catalog.records} == {_JAN_2024}


def test_chunk_stage_skips_the_lookup_for_an_undated_version(tmp_path: Path) -> None:
    store = LocalFSObjectStore(root=tmp_path)
    block_count = _write_representation(store=store)
    catalog = _ChunkCatalog(source=_source(sections=_sections(block_count=block_count)))
    _run_chunk_stage(catalog=catalog, store=store)
    assert catalog.lookups == []
    assert catalog.records
    assert {record.text_origin_at for record in catalog.records} == {None}


def test_chunk_stage_replay_never_recomputes_the_origin(tmp_path: Path) -> None:
    """A retried chunk stage keeps the recorded rows: no lookup, no new rows."""
    store = LocalFSObjectStore(root=tmp_path)
    block_count = _write_representation(store=store)
    catalog = _ChunkCatalog(
        source=_source(
            source_modified_at=_NOV_2025, sections=_sections(block_count=block_count)
        ),
        existing=(uuid4(),),
    )
    _run_chunk_stage(catalog=catalog, store=store)
    assert catalog.lookups == []
    assert catalog.records == ()


# -- E2: header date and asserted_at -------------------------------------------


def _chunk(*, text_origin_at: datetime | None) -> ChunkForEmbedding:
    end = _DOC_MD.index("\n")
    return ChunkForEmbedding(
        chunk_id=uuid4(),
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=0,
        char_start=0,
        char_end=end,
        chunk_content_hash="sha256:chunk",
        extraction_input_hash="sha256:input",
        section_role="body",
        section_path="0",
        text_origin_at=text_origin_at,
    )


def test_header_date_is_the_chunk_text_origin() -> None:
    source = _source(source_modified_at=_NOV_2025)
    header = _header_text(source=source, chunk=_chunk(text_origin_at=_DEC_2023))
    assert f"date {_DEC_2023.isoformat()};" in header
    assert _NOV_2025.isoformat() not in header


def test_pre_d140_chunk_falls_back_to_the_version_date() -> None:
    """A chunk row without a recorded origin behaves exactly as before D140."""
    legacy = _chunk(text_origin_at=None)
    assert _chunk_date(source=_source(source_modified_at=_NOV_2025), chunk=legacy) == (
        _NOV_2025
    )
    assert _chunk_date(source=_source(published_at=_JAN_2024), chunk=legacy) == (
        _JAN_2024
    )
    assert _chunk_date(source=_source(), chunk=legacy) is None
    assert "date unknown;" in _header_text(source=_source(), chunk=legacy)


def _grounded(*, source: ChunkSource, chunk: ChunkForEmbedding) -> object:
    """Ground one claim whose added context comes from the header date."""
    kept_ranges = ((chunk.char_start, chunk.char_end),)
    catalog = build_passage_catalog(
        blocks=blockize(document_md=_DOC_MD),
        target=chunk,
        previous=None,
        following=None,
        kept_ranges=kept_ranges,
    )
    label = next(
        passage.label
        for passage in catalog.passages
        if passage.char_start <= chunk.char_start < passage.char_end
    )
    return _grounded_claim(
        candidate=CandidateClaim(
            claim_text="As of 2023-12 the daily allowance is 50 EUR.",
            source_refs=(label,),
            added_context=(AddedContext(text="As of 2023-12", source_kind="header"),),
            entailment_self_verdict=True,
        ),
        source=source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=_DOC_MD,
        flagged_spans=set(),
        kept_ranges=kept_ranges,
        catalog=catalog,
    )


def test_fresh_claim_is_asserted_at_the_chunk_text_origin() -> None:
    """The claim takes the origin as asserted_at and grounds on the origin date."""
    source = _source(source_modified_at=_NOV_2025)
    record = _grounded(source=source, chunk=_chunk(text_origin_at=_DEC_2023))
    assert isinstance(record, ClaimRecord), record
    assert record.asserted_at == _DEC_2023
    # without the origin the header shows 2025-11, so the 2023 date is ungrounded
    assert not isinstance(
        _grounded(source=source, chunk=_chunk(text_origin_at=None)), ClaimRecord
    )


def test_pre_d140_claim_is_asserted_at_the_version_date() -> None:
    source = _source(source_modified_at=_DEC_2023)
    record = _grounded(source=source, chunk=_chunk(text_origin_at=None))
    assert isinstance(record, ClaimRecord), record
    assert record.asserted_at == _DEC_2023
