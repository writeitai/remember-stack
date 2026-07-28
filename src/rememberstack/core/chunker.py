"""The deterministic E1 chunker: anchor-stabilized packing of whole blocks (D58).

A chunk is an ordered run of whole blocks within one section, packed to a
token budget. Boundaries are stabilized by content anchors: a block whose hash
satisfies the anchor predicate forces a boundary before it, so an early edit
perturbs packing only up to the next anchor instead of rippling through the
document (e1 §4). Chunks never overlap and never split a block; an oversized
block becomes its own oversized chunk rather than being cut.

chunks = f(blocks, sections, budget, anchors, CHUNKER_VERSION): a parameter
change is a version bump and a cheap repack of existing atoms.
"""

import hashlib
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model import Block
from rememberstack.model import PackedChunk
from rememberstack.model import SectionSpan

CHUNKER_VERSION: Final = (
    "e1-chunker-2026.07c:whitespace-tokens:anchored:owner-runs"
    ":blockizer-heading-metadata"
)
"""Pins packing plus its blockizer-contract generation.

The ``07c`` bump follows D12/D57 because ``blocks.json`` gained D79 heading
metadata. Packing behavior and emitted chunk-grid bytes are unchanged from
``e1-chunker-2026.07b:whitespace-tokens:anchored:owner-runs``.
"""


class ChunkerParams(BaseModel):
    """The bound packing parameters (e1 §4). Values are starting points to
    measure (spike 3), never committed constants; changing them is a
    CHUNKER_VERSION bump."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_budget: int = Field(default=400, ge=1)
    anchor_modulus: int = Field(default=24, ge=1)
    anchor_min_gap_tokens: int = Field(default=200, ge=0)


def chunker_version(*, params: "ChunkerParams") -> str:
    """The complete packing-generation identity: algorithm + parameter values.

    chunks = f(blocks, sections, budget, anchors, version) — so changing any
    parameter re-keys packing automatically instead of silently replaying
    rows produced under different numbers (D58).
    """
    return (
        f"{CHUNKER_VERSION}"
        f":b{params.token_budget}"
        f"-m{params.anchor_modulus}"
        f"-g{params.anchor_min_gap_tokens}"
    )


def pack_blocks(
    *,
    blocks: tuple[Block, ...],
    sections: tuple[SectionSpan, ...],
    document_md: str,
    params: ChunkerParams,
) -> tuple[PackedChunk, ...]:
    """Pack the block grid into chunks, deepest-owner run by run.

    Every block belongs to exactly one DEEPEST section (D57): a leaf owns
    its whole range, and a parent directly owns the blocks none of its
    children cover (content before the first child, and gaps between
    children — the snap assigns those to the parent, never to a child).
    Each contiguous run of same-owner blocks packs independently, so no
    block is chunked twice and none is silently dropped (Codex review: a
    leaf-only walk lost every parent's direct content). Within a run,
    blocks accumulate greedily to the token budget; a chunk boundary is
    forced before every anchor block, and a block that alone exceeds the
    budget ships as its own oversized chunk. Sections are never crossed
    (§3 makes the partition well-defined).
    """
    chunks: list[PackedChunk] = []
    for section, run_start, run_end in _owner_runs(sections=sections):
        run_blocks = tuple(
            block for block in blocks if run_start <= block.ordinal <= run_end
        )
        chunks.extend(
            _pack_section(
                section=section,
                blocks=run_blocks,
                document_md=document_md,
                params=params,
                first_ordinal=len(chunks),
            )
        )
    return tuple(chunks)


def _owner_runs(
    *, sections: tuple[SectionSpan, ...]
) -> tuple[tuple[SectionSpan, int, int], ...]:
    """Every contiguous block run with its deepest-owning section, in
    document order: a section's runs are its range minus its direct
    children's ranges."""
    runs: list[tuple[SectionSpan, int, int]] = []
    for section in sections:
        prefix = f"{section.node_path}."
        child_ranges = sorted(
            (other.block_start, other.block_end)
            for other in sections
            if other.node_path.startswith(prefix)
            and "." not in other.node_path.removeprefix(prefix)
        )
        cursor = section.block_start
        for child_start, child_end in child_ranges:
            if child_start > cursor:
                runs.append((section, cursor, child_start - 1))
            cursor = max(cursor, child_end + 1)
        if cursor <= section.block_end:
            runs.append((section, cursor, section.block_end))
    return tuple(sorted(runs, key=lambda run: run[1]))


def chunk_content_hash(*, block_hashes: tuple[str, ...]) -> str:
    """The chunk identity: sha256 over the ordered block-hash sequence (D58)."""
    digest = hashlib.sha256("\n".join(block_hashes).encode("utf-8"))
    return digest.hexdigest()


def extraction_input_hash(
    *,
    own_block_hashes: tuple[str, ...],
    neighbor_block_hashes: tuple[str, ...],
    header_facts: tuple[str, ...],
    extractor_version: str,
    structurer_version: str,
) -> str:
    """The D56 reuse key: stable inputs of the E2 bundle, no LLM output.

    Own blocks + neighbor blocks + deterministic document metadata + the
    extractor and structurer versions. Prefixes, summaries, and section paths
    are carried forward on reuse, never keyed — so an unchanged key within a
    lineage means the prior claims are re-attached instead of re-extracted.
    """
    payload = "\x1e".join(
        (
            "\n".join(own_block_hashes),
            "\n".join(neighbor_block_hashes),
            "\n".join(header_facts),
            extractor_version,
            structurer_version,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def count_tokens(*, text: str) -> int:
    """The pinned token counter: whitespace tokens (part of CHUNKER_VERSION)."""
    return len(text.split())


def is_anchor(*, block_hash: str, params: ChunkerParams) -> bool:
    """The anchor predicate's hash half: uint64(block_hash) mod M == 0 (e1 §4)."""
    return int(block_hash[:16], 16) % params.anchor_modulus == 0


def _pack_section(
    *,
    section: SectionSpan,
    blocks: tuple[Block, ...],
    document_md: str,
    params: ChunkerParams,
    first_ordinal: int,
) -> tuple[PackedChunk, ...]:
    """Pack one section's blocks; boundaries never leave the section."""
    chunks: list[PackedChunk] = []
    run: list[tuple[Block, int]] = []
    run_tokens = 0
    tokens_since_anchor = params.anchor_min_gap_tokens  # first anchor never suppressed

    def flush() -> None:
        nonlocal run, run_tokens
        if not run:
            return
        run_blocks = tuple(block for block, _ in run)
        chunks.append(
            PackedChunk(
                ordinal=first_ordinal + len(chunks),
                section_id=section.section_id,
                block_start=run_blocks[0].ordinal,
                block_end=run_blocks[-1].ordinal,
                char_start=run_blocks[0].char_start,
                char_end=run_blocks[-1].char_end,
                chunk_content_hash=chunk_content_hash(
                    block_hashes=tuple(block.block_hash for block in run_blocks)
                ),
                token_count=run_tokens,
            )
        )
        run = []
        run_tokens = 0

    for block in blocks:
        tokens = count_tokens(text=document_md[block.char_start : block.char_end])
        anchored = (
            is_anchor(block_hash=block.block_hash, params=params)
            and tokens_since_anchor >= params.anchor_min_gap_tokens
        )
        if anchored:
            flush()  # packing after an anchor is independent of everything before
            tokens_since_anchor = 0
        elif run and run_tokens + tokens > params.token_budget:
            flush()  # budget boundary: the block starts the next run
        run.append((block, tokens))
        run_tokens += tokens
        tokens_since_anchor += tokens
        if run_tokens > params.token_budget:
            flush()  # a single oversized block ships as its own oversized chunk
    flush()
    return tuple(chunks)
