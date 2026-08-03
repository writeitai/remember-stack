"""Unit-speed proofs for D79's bounded, orientation-only E1/E2 consumption.

D80 retired the LLM location-prefix prompt. Summaries remain orientation-only
for E2 bundles and must not enter D56 extraction identity keys.
"""

from uuid import UUID

from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import PackedChunk
from rememberstack.model import SectionSpan
from rememberstack.workers.e1 import _chunk_record
from rememberstack.workers.e2 import _bundle_text
from rememberstack.workers.e2 import _location_bundle_line
from rememberstack.workers.e2 import _location_grounding_pairs
from rememberstack.workers.section_orientation import render_section_orientation
from rememberstack.workers.section_orientation import SECTION_ORIENTATION_MAX_CHARS

_DEPLOYMENT = UUID("82000000-0000-0000-0000-000000000001")
_DOC = UUID("82000000-0000-0000-0000-000000000002")
_VERSION = UUID("82000000-0000-0000-0000-000000000003")
_REPRESENTATION = UUID("82000000-0000-0000-0000-000000000004")
_TARGET_SECTION = UUID("82000000-0000-0000-0000-000000000007")


def _section(
    *,
    section_id: int,
    path: str,
    summary: str | None,
    block_start: int = 0,
    block_end: int = 0,
) -> SectionSpan:
    return SectionSpan(
        section_id=UUID(int=section_id),
        node_path=path,
        role="body",
        block_start=block_start,
        block_end=block_end,
        summary=summary,
    )


def _source(*, sections: tuple[SectionSpan, ...]) -> ChunkSource:
    return ChunkSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPRESENTATION,
        markdown_uri="mem://document.md",
        blocks_uri="mem://blocks.json",
        title="Orientation handbook",
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structurer",
        sections=sections,
    )


def _chunk(*, section_path: str = "0.2.1") -> ChunkForEmbedding:
    return ChunkForEmbedding(
        chunk_id=UUID("82000000-0000-0000-0000-000000000005"),
        doc_id=_DOC,
        version_id=_VERSION,
        ordinal=7,
        char_start=0,
        char_end=12,
        chunk_content_hash="sha256:chunk",
        extraction_input_hash="sha256:input",
        section_role="body",
        section_path=section_path,
        section_title="Bounded orientation",
        context_prefix="Existing source-derived prefix.",
        prefixer_version="test-prefixer",
    )


def _orientation_sections() -> tuple[SectionSpan, ...]:
    return (
        _section(section_id=1, path="0", summary="The complete handbook."),
        _section(section_id=2, path="0.2", summary="Chapter two covers routing."),
        _section(
            section_id=_TARGET_SECTION.int,
            path="0.2.1",
            summary="This subsection explains bounded orientation.",
        ),
        _section(section_id=4, path="0.9", summary="A sibling must never leak."),
    )


def test_section_orientation_contains_target_and_ancestor_summaries() -> None:
    """Orientation rendering still walks target then nearest ancestors."""
    orientation = render_section_orientation(
        sections=_orientation_sections(), target_path="0.2.1"
    )
    assert orientation is not None
    target = "TARGET 0.2.1: This subsection explains bounded orientation."
    chapter = "ANCESTOR 0.2: Chapter two covers routing."
    root = "ANCESTOR 0: The complete handbook."
    assert target in orientation
    assert chapter in orientation
    assert root in orientation
    assert orientation.index(target) < orientation.index(chapter) < orientation.index(
        root
    )
    assert "A sibling must never leak." not in orientation


def test_section_orientation_is_hard_capped_with_ellipsis() -> None:
    """One shared named cap bounds the complete target + ancestor rendering."""
    sections = (
        _section(section_id=1, path="0", summary="root"),
        _section(
            section_id=_TARGET_SECTION.int,
            path="0.2.1",
            summary="x" * (SECTION_ORIENTATION_MAX_CHARS * 2),
        ),
    )
    orientation = render_section_orientation(sections=sections, target_path="0.2.1")

    assert orientation is not None
    assert len(orientation) == SECTION_ORIENTATION_MAX_CHARS
    assert orientation.startswith("TARGET 0.2.1: ")
    assert orientation.endswith("…")
    assert "ANCESTOR 0:" not in orientation


def test_location_bundle_never_echoes_freeform_prefix() -> None:
    """D80: free-form context_prefix is not a bundle member (§3.3)."""
    line = _location_bundle_line(chunk=_chunk())
    assert line == "(none)" or "Existing source-derived prefix" not in line
    pairs = _location_grounding_pairs(chunk=_chunk())
    assert all(kind != "section_role" for kind, _ in pairs)
    assert "section_title" in {kind for kind, _ in pairs}


def test_summaries_do_not_enter_extraction_input_hash() -> None:
    """Two chunk-record runs differing only in summaries keep the D56 key."""
    packed = (
        PackedChunk(
            ordinal=0,
            section_id=_TARGET_SECTION,
            block_start=0,
            block_end=0,
            char_start=0,
            char_end=12,
            chunk_content_hash="sha256:chunk",
            token_count=3,
        ),
    )
    with_summaries = _chunk_record(
        source=_source(sections=_orientation_sections()),
        packed=packed,
        index=0,
        chunker_version="test-chunker",
    )
    without_summaries = _chunk_record(
        source=_source(
            sections=(
                _section(section_id=1, path="0", summary=None),
                _section(
                    section_id=_TARGET_SECTION.int, path="0.2.1", summary=None
                ),
            )
        ),
        packed=packed,
        index=0,
        chunker_version="test-chunker",
    )
    assert with_summaries.extraction_input_hash == without_summaries.extraction_input_hash


def test_extraction_bundle_keeps_orientation_separate_from_location() -> None:
    """E2 bundle may orient with summaries but location line stays typed-only."""
    document_md = "Target passage."
    chunks = (_chunk(),)
    source = _source(sections=_orientation_sections())
    text = _bundle_text(
        source=source, chunks=chunks, index=0, document_md=document_md
    )
    # Summaries may still appear as orientation in the extractor prompt when
    # the E2 path includes them — but free-form context_prefix must not.
    assert "Existing source-derived prefix." not in text
    assert "state where this passage sits" not in text
