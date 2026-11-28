"""D162 covers the incident shapes with the actual renderer and tokenizer."""

from base64 import b85encode
import hashlib
from uuid import UUID

import pytest

from rememberstack.core import blockize
from rememberstack.core import ChunkerParams
from rememberstack.core import pack_blocks
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
from rememberstack.model import Block
from rememberstack.model import BlockType
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import DerivationRange
from rememberstack.model import PackedChunk
from rememberstack.model import SectionSpan
from rememberstack.model.conversion import PageLocator
from rememberstack.model.conversion import SourceMapEntry

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


def _block(ordinal: int, start: int, end: int) -> Block:
    return Block(
        ordinal=ordinal,
        char_start=start,
        char_end=end,
        type=BlockType.PARAGRAPH,
        block_hash=str(ordinal),
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

    kwargs = dict(
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
    pieces = split_oversized_chunks(**kwargs)
    assert pieces == split_oversized_chunks(**kwargs)
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

    def row(piece: PackedChunk) -> ChunkForEmbedding:
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

    catalog = build_passage_catalog(
        blocks=blocks,
        target=row(pieces[1]),
        previous=row(pieces[0]),
        following=row(pieces[2]),
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
