"""Unit-speed proofs for D79's bounded, orientation-only E1/E2 consumption."""

from uuid import UUID

from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import PackedChunk
from rememberstack.model import SectionSpan
from rememberstack.workers.e1 import _chunk_record
from rememberstack.workers.e1 import _prefix_prompt
from rememberstack.workers.e2 import _bundle_text
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


def test_e1_prefix_prompt_contains_target_and_ancestor_summaries() -> None:
    """The fresh-prefix call sees local orientation, nearest ancestor first."""
    prompt = _prefix_prompt(
        source=_source(sections=_orientation_sections()),
        chunk=_chunk(),
        head="Target text.",
    )

    target = "TARGET 0.2.1: This subsection explains bounded orientation."
    chapter = "ANCESTOR 0.2: Chapter two covers routing."
    root = "ANCESTOR 0: The complete handbook."
    assert target in prompt
    assert chapter in prompt
    assert root in prompt
    assert prompt.index(target) < prompt.index(chapter) < prompt.index(root)
    assert "A sibling must never leak." not in prompt


def test_e1_prefix_prompt_is_hard_capped_with_ellipsis() -> None:
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


def test_e1_absent_summaries_preserve_the_pre_wave_prompt_shape() -> None:
    """A degraded generation contributes zero bytes to the prefix prompt."""
    prompt = _prefix_prompt(
        source=_source(
            sections=(
                _section(section_id=1, path="0", summary=None),
                _section(section_id=_TARGET_SECTION.int, path="0.2.1", summary=None),
            )
        ),
        chunk=_chunk(),
        head="Target text.",
    )

    assert prompt == (
        "In one sentence, state where this passage sits in the document — "
        "document title, section, and what surrounds it. Passage from "
        "'Orientation handbook', section path 0.2.1, chunk 7:\n\nTarget text."
    )


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
            chunk_content_hash="sha256:stable-blocks",
            token_count=2,
        ),
    )
    before = _chunk_record(
        source=_source(
            sections=(
                _section(section_id=1, path="0", summary="Old root orientation."),
                _section(
                    section_id=_TARGET_SECTION.int,
                    path="0.2.1",
                    summary="Old local orientation.",
                ),
            )
        ),
        packed=packed,
        index=0,
        chunker_version="test-chunker",
    )
    after = _chunk_record(
        source=_source(
            sections=(
                _section(section_id=1, path="0", summary="New root orientation."),
                _section(
                    section_id=_TARGET_SECTION.int,
                    path="0.2.1",
                    summary="New local orientation.",
                ),
            )
        ),
        packed=packed,
        index=0,
        chunker_version="test-chunker",
    )

    assert before.extraction_input_hash == after.extraction_input_hash


def test_e2_bundle_places_section_summaries_before_context_prefix() -> None:
    """The D31 element is present but remains separate from grounding kinds."""
    document_md = "Target text."
    bundle = _bundle_text(
        source=_source(sections=_orientation_sections()),
        chunks=(_chunk(),),
        index=0,
        document_md=document_md,
    )

    section_at = bundle.index("SECTION: path 0.2.1, role body")
    summaries_at = bundle.index(
        "SECTION SUMMARIES (orientation only; never quote as source):"
    )
    prefix_at = bundle.index("CONTEXT PREFIX: Existing source-derived prefix.")
    assert section_at < summaries_at < prefix_at
    assert "TARGET 0.2.1: This subsection explains bounded orientation." in bundle
    assert "ANCESTOR 0.2: Chapter two covers routing." in bundle
    assert "ANCESTOR 0: The complete handbook." in bundle


def test_e2_bundle_renders_none_for_a_degraded_generation() -> None:
    """E2 keeps the element with the bundle's '(none)' idiom when every
    summary is null — asymmetric with E1's zero bytes by design (the bundle
    is a fixed element contract, the prefix prompt is not)."""
    sections = tuple(
        _section(section_id=index + 1, path=path, summary=None)
        for index, path in enumerate(("0", "0.2", "0.2.1"))
    )
    bundle = _bundle_text(
        source=_source(sections=sections),
        chunks=(_chunk(),),
        index=0,
        document_md="chunk body...",
    )
    assert "SECTION SUMMARIES (orientation only; never quote as source):" in bundle
    assert "\n(none)\n" in bundle


def test_orientation_degrades_on_cross_generation_section_mismatch() -> None:
    """A chunk cut under a superseded skeleton must not attach the current
    generation's summaries via a merely-coincident node path (review): when
    the chunk's section id does not match the current row at that path, the
    whole rendering degrades to None."""
    sections = _orientation_sections()
    assert (
        render_section_orientation(
            sections=sections, target_path="0.2.1", target_section_id=_TARGET_SECTION
        )
        is not None
    )
    assert (
        render_section_orientation(
            sections=sections, target_path="0.2.1", target_section_id=UUID(int=999)
        )
        is None
    )
