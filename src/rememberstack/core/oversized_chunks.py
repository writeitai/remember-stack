"""Lossless D162 cuts of oversized D58 candidates in E0 coordinates."""

from bisect import bisect_left
from bisect import bisect_right
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
import hashlib
from operator import attrgetter
import re

from rememberstack.core.chunker import count_tokens
from rememberstack.core.extraction_eligibility import INELIGIBLE_DERIVATION_KINDS
from rememberstack.model import Block
from rememberstack.model import BlockType
from rememberstack.model import DerivationRange
from rememberstack.model import PackedChunk
from rememberstack.model.conversion import SourceMapEntry

_NONBLANK = re.compile(r"\S")
_SENTENCES = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)")
_MARKDOWN_ROW_ENDS = re.compile(r"\r\n|[\n\r]")
_LINES = re.compile(r"\r\n|[\n\r\v\f\x85\u2028\u2029]")
_SPACES = re.compile(r"\s+")


def split_oversized_chunks(
    *,
    packed: tuple[PackedChunk, ...],
    oversized: frozenset[int],
    blocks: tuple[Block, ...],
    eligibility: tuple[bool, ...],
    document_md: str,
    ranges: tuple[DerivationRange, ...],
    source_map: tuple[SourceMapEntry, ...],
    fits: Callable[[PackedChunk, int], bool],
) -> tuple[PackedChunk, ...]:
    """Keep fitting candidates and recheck if the final count changes D80 mode."""
    block_indexes = {block.ordinal: index for index, block in enumerate(blocks)}
    recorded_seams = tuple(
        sorted(
            {
                offset
                for entry in source_map
                if entry.region_kind in {"turn", "table_row", "row", "page"}
                or any(
                    (locator.kind == "page" and locator.precision == "page")
                    or (locator.kind == "time" and locator.precision == "segment")
                    for locator in entry.locators
                )
                for offset in (entry.start, entry.end)
            }
        )
    )
    while oversized:
        split: list[PackedChunk] = []
        for candidate in packed:
            if candidate.ordinal not in oversized:
                split.append(candidate)
                continue
            first = block_indexes[candidate.block_start]
            last = block_indexes[candidate.block_end] + 1
            split.extend(
                _split_candidate(
                    candidate=candidate,
                    blocks=blocks[first:last],
                    all_blocks=blocks,
                    eligibility=eligibility[first:last],
                    document_md=document_md,
                    ranges=ranges,
                    recorded_seams=recorded_seams,
                    fits=fits,
                    chunk_count=len(packed),
                )
            )
        count_changed = len(packed) != len(split)
        packed = tuple(
            chunk
            if chunk.ordinal == ordinal
            else chunk.model_copy(update={"ordinal": ordinal})
            for ordinal, chunk in enumerate(split)
        )
        oversized = (
            frozenset(chunk.ordinal for chunk in packed if not fits(chunk, len(packed)))
            if count_changed
            else frozenset()
        )
    return packed


def _split_candidate(
    *,
    candidate: PackedChunk,
    blocks: tuple[Block, ...],
    all_blocks: tuple[Block, ...],
    eligibility: tuple[bool, ...],
    document_md: str,
    ranges: tuple[DerivationRange, ...],
    recorded_seams: tuple[int, ...],
    fits: Callable[[PackedChunk, int], bool],
    chunk_count: int,
) -> Iterator[PackedChunk]:
    start, end = candidate.char_start, candidate.char_end
    nonempty_blocks = tuple(
        block
        for block in blocks
        if block.char_start < block.char_end
        and block.char_start < end
        and start < block.char_end
    )
    block_starts = tuple(block.char_start for block in nonempty_blocks)
    block_ends = tuple(block.char_end for block in nonempty_blocks)
    block_seams = (*block_starts, *block_ends)
    recorded = (
        *recorded_seams[
            bisect_right(recorded_seams, start) : bisect_left(recorded_seams, end)
        ],
        *(
            row.end()
            for block in nonempty_blocks
            if block.type is BlockType.TABLE
            for row in _MARKDOWN_ROW_ENDS.finditer(
                document_md, max(start, block.char_start), min(end, block.char_end)
            )
        ),
    )
    pending = list(
        reversed(
            tuple(
                _eligibility_pieces(
                    start=start,
                    end=end,
                    blocks=blocks,
                    eligibility=eligibility,
                    document_md=document_md,
                    ranges=ranges,
                )
            )
        )
    )
    while pending:
        left, right, eligible = pending.pop()
        first_index = bisect_right(block_ends, left)
        last_index = bisect_left(block_starts, right)
        intersected = nonempty_blocks[first_index:last_index]
        if intersected:
            block_start, block_end = intersected[0].ordinal, intersected[-1].ordinal
        else:
            # On a D80 recheck, the candidate may start in a gap whose
            # preceding block is outside the candidate's stored block bounds.
            preceding = bisect_right(all_blocks, left, key=attrgetter("char_end")) - 1
            block_start = block_end = all_blocks[max(0, preceding)].ordinal
        piece_text = document_md[left:right]
        piece = candidate.model_copy(
            update={
                "span_kind": "split",
                "char_start": left,
                "char_end": right,
                "block_start": block_start,
                "block_end": block_end,
                "chunk_content_hash": _fragment_hash(
                    start=left, end=right, blocks=intersected, document_md=document_md
                ),
                "token_count": count_tokens(text=piece_text),
                "extraction_eligible": eligible,
            }
        )
        if fits(piece, chunk_count):
            yield piece
            continue
        split_at = left + _size_cut(
            text=piece_text,
            block_seams=(at - left for at in block_seams if left < at < right),
            recorded_seams=(at - left for at in recorded if left < at < right),
        )
        pending.extend(((split_at, right, eligible), (left, split_at, eligible)))


def _eligibility_pieces(
    *,
    start: int,
    end: int,
    blocks: tuple[Block, ...],
    eligibility: tuple[bool, ...],
    document_md: str,
    ranges: tuple[DerivationRange, ...],
) -> Iterator[tuple[int, int, bool]]:
    """Use E0 block eligibility and classify nonblank inter-block gaps by label."""
    cursor = start
    atoms: list[tuple[int, int, bool]] = []
    for block, eligible in zip(blocks, eligibility, strict=True):
        left, right = max(start, block.char_start), min(end, block.char_end)
        if left >= right:
            continue
        atoms.extend(_gap(start=cursor, end=left, ranges=ranges))
        atoms.append((left, right, eligible))
        cursor = right
    atoms.extend(_gap(start=cursor, end=end, ranges=ranges))
    piece_start = start
    current = None
    for left, right, eligible in atoms:
        match = _NONBLANK.search(document_md, left, right)
        if match is None:
            continue
        if current is not None and eligible != current:
            yield piece_start, match.start(), current
            piece_start = match.start()
        current = eligible
    if current is not None:
        yield piece_start, end, current


def _gap(
    *, start: int, end: int, ranges: tuple[DerivationRange, ...]
) -> Iterator[tuple[int, int, bool]]:
    if start >= end:
        return
    cursor = start
    for label in ranges:
        if label.end <= start:
            continue
        if label.start >= end:
            break
        if cursor < label.start:
            yield cursor, label.start, True
        left, right = max(start, label.start), min(end, label.end)
        yield left, right, label.derivation_kind not in INELIGIBLE_DERIVATION_KINDS
        cursor = right
    if cursor < end:
        yield cursor, end, True


def _size_cut(
    *, text: str, block_seams: Iterable[int], recorded_seams: Iterable[int]
) -> int:
    """Choose the first legal seam tier, nearest midpoint with earlier ties."""
    first = len(text) - len(text.lstrip())
    last = len(text.rstrip()) - 1

    def legal(offsets: Iterable[int]) -> Iterator[int]:
        for at in offsets:
            if first < at <= last:
                spaces = _SPACES.match(text, at)
                yield spaces.end() if spaces else at

    for offsets in (
        block_seams,
        recorded_seams,
        (match.end() for match in _SENTENCES.finditer(text)),
        (match.end() for match in _LINES.finditer(text)),
        (match.end() for match in _SPACES.finditer(text) if match.start() > first),
    ):
        at = min(
            legal(offsets),
            key=lambda seam: (abs(len(text) - 2 * seam), seam),
            default=None,
        )
        if at is not None:
            return at

    if first >= last:
        raise ValueError("D162 cannot fit one nonblank scalar with its location header")
    # Python string offsets are Unicode scalar boundaries. A CRLF contains
    # whitespace and is consumed as a unit by the tiers above.
    return min(max(len(text) // 2, first + 1), last)


def _fragment_hash(
    *, start: int, end: int, blocks: tuple[Block, ...], document_md: str
) -> str:
    """Frame the exact ordered block fragments and gap bytes without offsets."""
    digest = hashlib.sha256(b"rememberstack:split-chunk:d162\0")

    def add(kind: bytes, left: int, right: int) -> None:
        if left < right:
            payload = document_md[left:right].encode("utf-8")
            digest.update(kind)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)

    cursor = start
    for block in blocks:
        left, right = max(start, block.char_start), min(end, block.char_end)
        add(b"G", cursor, left)
        add(b"B", left, right)
        cursor = right
    add(b"G", cursor, end)
    return digest.hexdigest()
