"""D162 covers the incident shapes with the actual renderer and tokenizer."""

from base64 import b85encode
import hashlib
from uuid import UUID

import pytest

from rememberstack.core import blockize
from rememberstack.core import ChunkerParams
from rememberstack.core import pack_blocks
from rememberstack.core.embedding_input_policy import EmbeddingInputRender
from rememberstack.core.embedding_input_policy import H_MAX
from rememberstack.core.embedding_input_policy import LocationFacts
from rememberstack.core.embedding_input_policy import render_embedding_input
from rememberstack.core.extraction_eligibility import block_eligibility
from rememberstack.core.hosted_embedding_ceiling import hosted_embedding_fits
from rememberstack.core.hosted_embedding_ceiling import HOSTED_EMBEDDING_TOKEN_CEILING
from rememberstack.core.hosted_embedding_ceiling import hosted_embedding_tokens
from rememberstack.core.oversized_chunks import _size_cut
from rememberstack.core.oversized_chunks import split_oversized_chunks
from rememberstack.core.source_passages import build_passage_catalog
from rememberstack.core.source_passages import render_passage_catalog
from rememberstack.core.source_passages import resolve_source_refs
from rememberstack.model import Block
from rememberstack.model import BlockType
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import DerivationRange
from rememberstack.model import PackedChunk
from rememberstack.model import SectionSpan
from rememberstack.model.conversion import PageLocator
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.workers.extraction_references import render_selection_passages

_SECTION = UUID(int=1)
_FACTS = LocationFacts(
    chunk_id=UUID(int=2),
    doc_id=UUID(int=3),
    version_id=UUID(int=4),
    title="Deployment handbook",
    source_kind="file",
    section_title="Deployment settings",
    section_path="0",
    section_role="body",
    chunk_count=1,
)


def _render(text: str, count: int) -> str:
    return render_embedding_input(
        facts=_FACTS.model_copy(update={"chunk_count": count}), body=text
    ).embedding_text


def _opaque(index: int, length: int) -> str:
    return b85encode(hashlib.shake_128(str(index).encode()).digest(length)).decode()


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        (
            "indented-yaml",
            "".join(
                f'{" " * 27}key_{i:03d}: "{_opaque(i, 80)}" # generated\r\n'
                for i in range(470)
            ).rstrip(),
            ((17811, 10291), (35747, 10377), (53531, 10275), (71438, 10342)),
        ),
        (
            "long-markdown-section",
            "## Deployment\n\n"
            + " ".join(f"Setting `{_opaque(i, 77)}`." for i in range(306)),
            ((13, 12), (16692, 12055), (33368, 12089)),
        ),
        (
            "giant-no-break-line",
            "🙂界💡🚀" * 14_000,
            ((14000, 14006), (28000, 14006), (42000, 14006), (56000, 14006)),
        ),
    ],
)
def test_incident_shapes(
    name: str, body: str, expected: tuple[tuple[int, int], ...]
) -> None:
    blocks = blockize(document_md=body)
    section = SectionSpan(
        section_id=_SECTION,
        node_path="0",
        role="body",
        title="Deployment settings",
        block_start=0,
        block_end=len(blocks) - 1,
    )
    legacy = pack_blocks(
        blocks=blocks, sections=(section,), document_md=body, params=ChunkerParams()
    )

    def fits(chunk: PackedChunk, count: int) -> bool:
        return hosted_embedding_fits(
            render_embedding_input(
                facts=_FACTS.model_copy(update={"chunk_count": count}),
                body=body[chunk.char_start : chunk.char_end],
            )
        )

    oversized = frozenset(
        chunk.ordinal for chunk in legacy if not fits(chunk, len(legacy))
    )
    assert oversized
    pieces = split_oversized_chunks(
        packed=legacy,
        oversized=oversized,
        blocks=blocks,
        eligibility=(True,) * len(blocks),
        document_md=body,
        ranges=(),
        source_map=(),
        fits=fits,
    )
    assert b"".join(
        body[p.char_start : p.char_end].encode() for p in pieces
    ) == b"".join(body[c.char_start : c.char_end].encode() for c in legacy)
    assert all(piece.section_id == _SECTION for piece in pieces)
    assert all(body[p.char_start : p.char_end].strip() for p in pieces)
    assert all(not (body[p.char_end - 1 : p.char_end + 1] == "\r\n") for p in pieces)
    assert [p.ordinal for p in pieces] == list(range(len(pieces)))
    measured = tuple(
        (
            p.char_end,
            hosted_embedding_tokens(
                _render(body[p.char_start : p.char_end], len(pieces))
            ),
        )
        for p in pieces
    )
    assert all(count <= HOSTED_EMBEDDING_TOKEN_CEILING for _, count in measured)
    print(name, len(body), measured)
    assert measured == expected


def test_pinned_special_token_and_maximum_header_budget() -> None:
    assert hosted_embedding_tokens("") == 1
    assert hosted_embedding_tokens("hello") == 2
    # A UTF-8 scalar has at most four byte tokens. D80 bounds every header
    # to H_MAX scalars; the separator and the pinned end token also fit.
    assert 4 * (H_MAX + len("\n\n") + 1) + 1 < HOSTED_EMBEDDING_TOKEN_CEILING
    assert (
        hosted_embedding_tokens(("🧬" * H_MAX) + "\n\n𐍈")
        < HOSTED_EMBEDDING_TOKEN_CEILING
    )


def test_split_tiers_keep_crlf_and_attach_separator_to_left() -> None:
    assert _size_cut(text="a  b  c  d", block_seams=(1, 7), recorded_seams=(4,)) == 3
    assert (
        _size_cut(
            text="First.\r\n  Second\r\n  Third", block_seams=(), recorded_seams=()
        )
        == 10
    )
    assert _size_cut(text="aa bb\r\ncc dd", block_seams=(), recorded_seams=(2,)) == 3
    assert _size_cut(text="🙂🙃🙂🙃", block_seams=(), recorded_seams=()) == 2


@pytest.mark.parametrize("row_end", ("\n", "\r\n", "\r"))
def test_table_row_seam_ignores_in_cell_unicode_line_separator(row_end: str) -> None:
    header = f"| Data |{row_end}| --- |{row_end}"
    first_row = "| " + "🙂界" * 5_000 + " |" + row_end
    second_row = "| " + "🙂界" * 500 + "\u2028" + "🙂界" * 500 + " |" + row_end
    body = header + first_row + second_row + "| " + "🙂界" * 5_000 + " |"
    block = _block(0, 0, len(body)).model_copy(update={"type": BlockType.TABLE})
    section = SectionSpan(
        section_id=_SECTION, node_path="0", role="body", block_start=0, block_end=0
    )
    packed = pack_blocks(
        blocks=(block,), sections=(section,), document_md=body, params=ChunkerParams()
    )

    def fits(chunk: PackedChunk, count: int) -> bool:
        return (
            hosted_embedding_tokens(
                _render(body[chunk.char_start : chunk.char_end], count)
            )
            <= HOSTED_EMBEDDING_TOKEN_CEILING
        )

    assert not fits(packed[0], len(packed))
    left, right = split_oversized_chunks(
        packed=packed,
        oversized=frozenset({0}),
        blocks=(block,),
        eligibility=(True,),
        document_md=body,
        ranges=(),
        source_map=(),
        fits=fits,
    )
    assert left.char_start == 0
    assert left.char_end == right.char_start == len(header + first_row)
    assert right.char_end == len(body)
    assert fits(left, 2) and fits(right, 2)


def _block(ordinal: int, start: int, end: int) -> Block:
    return Block(
        ordinal=ordinal,
        char_start=start,
        char_end=end,
        type=BlockType.PARAGRAPH,
        block_hash=str(ordinal),
    )


def _chunk_row(piece: PackedChunk) -> ChunkForEmbedding:
    return ChunkForEmbedding(
        chunk_id=UUID(int=100 + piece.ordinal),
        doc_id=_FACTS.doc_id,
        version_id=_FACTS.version_id,
        ordinal=piece.ordinal,
        char_start=piece.char_start,
        char_end=piece.char_end,
        chunk_content_hash=piece.chunk_content_hash,
        extraction_input_hash="unused",
        section_role="body",
        section_path="0",
        span_kind=piece.span_kind,
    )


def test_eligibility_gap_and_recorded_seam_cover_exact_bytes() -> None:
    body = "AB  hidden  \r\n   standalone clause  CD"
    gap_start = body.index("hidden")
    prose_start = body.index("standalone")
    second_block_start = body.index("CD")
    blocks = (_block(0, 0, 2), _block(1, second_block_start, len(body)))
    ranges = (
        DerivationRange(
            start=gap_start,
            end=prose_start,
            derivation_kind="config",
            evidence_mode="source_expression",
        ),
    )
    section = SectionSpan(
        section_id=_SECTION, node_path="0", role="body", block_start=0, block_end=1
    )
    candidates = pack_blocks(
        blocks=blocks,
        sections=(section,),
        document_md=body,
        params=ChunkerParams(anchor_min_gap_tokens=100_000),
    )

    def fits(chunk: PackedChunk, count: int) -> bool:
        return chunk.char_end - chunk.char_start <= 20

    def split() -> tuple[PackedChunk, ...]:
        return split_oversized_chunks(
            packed=candidates,
            oversized=frozenset({0}),
            blocks=blocks,
            eligibility=block_eligibility(blocks=blocks, ranges=ranges),
            document_md=body,
            ranges=ranges,
            source_map=(
                SourceMapEntry(
                    start=prose_start,
                    end=second_block_start,
                    locators=(PageLocator(page=1, precision="page"),),
                ),
            ),
            fits=fits,
        )

    pieces = split()
    assert pieces == split()
    assert [
        (
            body[p.char_start : p.char_end],
            p.extraction_eligible,
            p.block_start,
            p.block_end,
        )
        for p in pieces
    ] == [
        ("AB  ", True, 0, 0),
        ("hidden  \r\n   ", False, 0, 0),
        ("standalone clause  ", True, 0, 0),
        ("CD", True, 1, 1),
    ]

    catalog = build_passage_catalog(
        blocks=blocks,
        document_md=body,
        target=_chunk_row(pieces[1]),
        previous=_chunk_row(pieces[0]),
        following=_chunk_row(pieces[2]),
        kept_ranges=((gap_start, gap_start + len("hidden")),),
    )
    assert any(
        passage.region == "target"
        and passage.origin_eligible
        and (passage.char_start, passage.char_end) == (gap_start, prose_start)
        for passage in catalog.passages
    )


def test_final_grid_rechecks_when_d80_gains_a_header() -> None:
    text = "a" * 40
    block = _block(0, 0, 40)
    legacy = PackedChunk(
        ordinal=0,
        section_id=_SECTION,
        block_start=0,
        block_end=0,
        char_start=0,
        char_end=len(text),
        chunk_content_hash="legacy",
        token_count=1,
    )
    pieces = split_oversized_chunks(
        packed=(legacy,),
        oversized=frozenset({0}),
        blocks=(block,),
        eligibility=(True,),
        document_md=text,
        ranges=(),
        source_map=(),
        fits=lambda piece, count: (
            piece.char_end - piece.char_start <= (20 if count == 1 else 15)
        ),
    )
    assert [(p.char_start, p.char_end) for p in pieces] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 40),
    ]


def test_header_recheck_assigns_gap_only_pieces_to_original_preceding_block() -> None:
    body = f"Lead.\n\n[link]: https://example.test/{'🙂' * 16373}\n\nTail."
    blocks = blockize(document_md=body)
    assert len(blocks) == 2  # The Markdown definition is an inter-block gap.
    section = SectionSpan(
        section_id=_SECTION, node_path="0", role="body", block_start=0, block_end=1
    )
    packed = pack_blocks(
        blocks=blocks, sections=(section,), document_md=body, params=ChunkerParams()
    )
    assert len(packed) == 1

    def rendered(text: str, count: int) -> EmbeddingInputRender:
        return render_embedding_input(
            facts=_FACTS.model_copy(
                update={"chunk_count": count, "source_shape": "transcript"}
            ),
            body=text,
        )

    def fits(chunk: PackedChunk, count: int) -> bool:
        return hosted_embedding_fits(
            rendered(body[chunk.char_start : chunk.char_end], count)
        )

    assert not fits(packed[0], 1)
    remainder = body[body.index("[link]") :]
    assert hosted_embedding_tokens(rendered(remainder, 1).embedding_text) == 16_384
    assert not hosted_embedding_fits(rendered(remainder, 2))

    pieces = split_oversized_chunks(
        packed=packed,
        oversized=frozenset({0}),
        blocks=blocks,
        eligibility=(True, True),
        document_md=body,
        ranges=(),
        source_map=(),
        fits=fits,
    )
    gap_pieces = [
        piece
        for piece in pieces
        if blocks[0].char_end <= piece.char_start
        and piece.char_end <= blocks[1].char_start
    ]
    assert [(piece.block_start, piece.block_end) for piece in gap_pieces] == [
        (0, 0),
        (0, 0),
    ]
    assert all(fits(piece, len(pieces)) for piece in pieces)
    assert "".join(body[piece.char_start : piece.char_end] for piece in pieces) == body


def test_split_link_definition_with_indentation_is_citable_in_both_stages() -> None:
    body = f"Lead.\n\n[link]: https://example.test/{'🙂' * 16373}\n\n   Tail."
    blocks = blockize(document_md=body)
    section = SectionSpan(
        section_id=_SECTION, node_path="0", role="body", block_start=0, block_end=1
    )
    packed = pack_blocks(
        blocks=blocks, sections=(section,), document_md=body, params=ChunkerParams()
    )

    def fits(chunk: PackedChunk, count: int) -> bool:
        return (
            hosted_embedding_tokens(
                _render(body[chunk.char_start : chunk.char_end], count)
            )
            <= HOSTED_EMBEDDING_TOKEN_CEILING
        )

    assert not fits(packed[0], 1)
    pieces = split_oversized_chunks(
        packed=packed,
        oversized=frozenset({0}),
        blocks=blocks,
        eligibility=(True, True),
        document_md=body,
        ranges=(),
        source_map=(),
        fits=fits,
    )
    previous, target, following = map(_chunk_row, pieces[-3:])
    assert target.char_start < blocks[1].char_start
    assert body[blocks[1].char_start : target.char_end] == "   "
    window = (target.char_start, target.char_end)
    target_text = body[target.char_start : target.char_end]

    selection = build_passage_catalog(
        blocks=blocks,
        document_md=body,
        target=target,
        previous=previous,
        following=following,
        kept_ranges=(),
    )
    (source,) = (
        passage for passage in selection.passages if passage.region == "target"
    )
    assert (source.char_start, source.char_end) == window
    assert target_text in render_selection_passages(catalog=selection, document_md=body)

    claimify = build_passage_catalog(
        blocks=blocks,
        document_md=body,
        target=target,
        previous=previous,
        following=following,
        kept_ranges=((target.char_start, target.char_start + 1),),
    )
    assert target_text in render_passage_catalog(catalog=claimify, document_md=body)
    exact = next(
        passage
        for passage in claimify.passages
        if (passage.char_start, passage.char_end) == window
    )
    evidence = resolve_source_refs(refs=(exact.label,), catalog=claimify)
    assert (evidence.origin.char_start, evidence.origin.char_end) == window


def test_split_evidence_clips_only_exact_linear_locators() -> None:
    from rememberstack.model import EvidenceSpan
    from rememberstack.model.conversion import SourceRangeLocator
    from rememberstack.model.occurrence_provenance import (
        resolve_spans_occurrence_provenance,
    )

    page = PageLocator(page=2, precision="page")
    exact = SourceRangeLocator(start_offset=100, end_offset=120, precision="exact")
    envelope = SourceRangeLocator(start_offset=200, end_offset=220, precision="exact")
    ranges = (
        DerivationRange(
            start=10,
            end=30,
            derivation_kind="passthrough",
            evidence_mode="source_expression",
        ),
    )
    mapped = (
        SourceMapEntry(start=10, end=30, locators=(exact,), linear_source_offsets=True),
        SourceMapEntry(start=10, end=30, locators=(envelope,)),
        SourceMapEntry(start=10, end=30, locators=(page,)),
        SourceMapEntry(
            start=25, end=30, locators=(PageLocator(page=3, precision="page"),)
        ),
    )
    resolved = resolve_spans_occurrence_provenance(
        spans=(EvidenceSpan(char_start=13, char_end=18),),
        ranges=ranges,
        source_map=mapped,
        split_windows=((12, 24),),
    )
    assert "linear_source_offsets" not in mapped[1].model_dump(mode="json")
    assert resolved.derivation_kind == "passthrough"
    assert resolved.source_locators == (
        SourceRangeLocator(start_offset=103, end_offset=108, precision="exact"),
        envelope,
        page,
    )
