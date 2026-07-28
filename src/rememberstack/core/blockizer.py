"""The deterministic blockizer: document.md → the block sequence (D57, e1 §2).

One shared code path regardless of which converter produced the Markdown. The
parser profile is pinned (GFM: CommonMark + the table extension, via
markdown-it-py); normalization order is fixed (join hard-wrapped lines → NFC →
collapse internal whitespace → hash). Determinism is regression-tested by the
golden corpus in CI per `BLOCKIZER_VERSION` — drift is a version bump, never a
silent change.
"""

import hashlib
from typing import Final
import unicodedata

from markdown_it import MarkdownIt
from markdown_it.token import Token

from rememberstack.model import Block
from rememberstack.model import BlockType

BLOCKIZER_VERSION: Final = (
    "blockizer-2026.07b:markdown-it-py-4:gfm-tables:heading-metadata"
)
"""Pins the parser profile and D79 heading metadata contract.

The ``07b`` bump changes only ``blocks.json`` metadata. Segmentation, offsets,
normalization for ``block_hash``, and therefore the block/chunk grid are byte
identical to ``blockizer-2026.07:markdown-it-py-4:gfm-tables``.
"""

_ATOMIC_CONTAINERS: Final = {
    "table_open": ("table_close", BlockType.TABLE),
    "blockquote_open": ("blockquote_close", BlockType.QUOTE),
}
_LIST_OPENERS: Final = frozenset({"bullet_list_open", "ordered_list_open"})


def blockize(*, document_md: str) -> tuple[Block, ...]:
    """Derive the deterministic block sequence from a document.md rendering.

    Blocks are CommonMark block-level elements: paragraphs, headings, list
    items, atomic tables, code fences, and block quotes. Offsets slice
    `document_md` exactly; the hash is over the normalized text, so a pure
    reflow (hard-wrap change) does not change identity.
    """
    tokens = _parser().parse(document_md)
    line_offsets = _line_offsets(document_md=document_md)
    blocks: list[Block] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        consumed, block = _emit(
            tokens=tokens,
            index=index,
            document_md=document_md,
            line_offsets=line_offsets,
            ordinal=len(blocks),
        )
        if block is not None:
            blocks.append(block)
        index += consumed
        del token
    return tuple(blocks)


def normalized_block_text(*, raw: str) -> str:
    """Apply the fixed normalization order: join lines → NFC → collapse spaces."""
    joined = " ".join(line.strip() for line in raw.splitlines())
    composed = unicodedata.normalize("NFC", joined)
    return " ".join(composed.split())


def normalized_heading_title(*, title: str) -> str:
    """D79 title normalization: NFKC → casefold → whitespace collapse."""
    compatible = unicodedata.normalize("NFKC", title)
    return " ".join(compatible.casefold().split())


def block_hash(*, raw: str) -> str:
    """The block identity: sha256 over the normalized text."""
    digest = hashlib.sha256(normalized_block_text(raw=raw).encode("utf-8"))
    return digest.hexdigest()


def _parser() -> MarkdownIt:
    """The pinned GFM-profile parser: CommonMark plus the table extension."""
    return MarkdownIt("commonmark").enable("table")


def _line_offsets(*, document_md: str) -> tuple[int, ...]:
    """Char offset of each line start, plus the end-of-document sentinel."""
    offsets = [0]
    for line in document_md.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return tuple(offsets)


def _emit(
    *,
    tokens: list[Token],
    index: int,
    document_md: str,
    line_offsets: tuple[int, ...],
    ordinal: int,
) -> tuple[int, Block | None]:
    """Emit at most one block starting at tokens[index]; return tokens consumed.

    List containers recurse into their items; each top-level list item is one
    atomic block whose span includes any nested sub-list (deliberate: emitting
    nested items separately would create overlapping spans, and chunks require
    non-overlapping whole-block runs — e1 §4). Other containers and leaves map
    directly. Unknown structural tokens are skipped one at a time — the golden
    corpus locks the observable result.
    """
    token = tokens[index]
    if token.type in _LIST_OPENERS:
        return 1, None  # items are emitted individually; the wrapper is not a block
    if token.type == "list_item_open":
        close = _matching_close(
            tokens=tokens, index=index, close_type="list_item_close"
        )
        return (
            close - index + 1,
            _block_from_lines(
                token=token,
                block_type=BlockType.LIST_ITEM,
                document_md=document_md,
                line_offsets=line_offsets,
                ordinal=ordinal,
            ),
        )
    if token.type in _ATOMIC_CONTAINERS:
        close_type, block_type = _ATOMIC_CONTAINERS[token.type]
        close = _matching_close(tokens=tokens, index=index, close_type=close_type)
        return (
            close - index + 1,
            _block_from_lines(
                token=token,
                block_type=block_type,
                document_md=document_md,
                line_offsets=line_offsets,
                ordinal=ordinal,
            ),
        )
    if token.type == "heading_open":
        inline = tokens[index + 1]
        heading_title = _heading_text(token=inline)
        return 3, _block_from_lines(
            token=token,
            block_type=BlockType.HEADING,
            document_md=document_md,
            line_offsets=line_offsets,
            ordinal=ordinal,
            heading_level=int(token.tag.removeprefix("h")),
            heading_title=heading_title,
        )
    if token.type == "paragraph_open":
        return 3, _block_from_lines(
            token=token,
            block_type=BlockType.PARAGRAPH,
            document_md=document_md,
            line_offsets=line_offsets,
            ordinal=ordinal,
        )
    if token.type in ("fence", "code_block"):
        return 1, _block_from_lines(
            token=token,
            block_type=BlockType.CODE,
            document_md=document_md,
            line_offsets=line_offsets,
            ordinal=ordinal,
        )
    return 1, None


def _matching_close(*, tokens: list[Token], index: int, close_type: str) -> int:
    """Index of the container's matching close token at the same nesting level."""
    level = tokens[index].level
    for probe in range(index + 1, len(tokens)):
        if tokens[probe].type == close_type and tokens[probe].level == level:
            return probe
    raise ValueError(f"unbalanced container at token {index}: no {close_type}")


def _block_from_lines(
    *,
    token: Token,
    block_type: BlockType,
    document_md: str,
    line_offsets: tuple[int, ...],
    ordinal: int,
    heading_level: int | None = None,
    heading_title: str | None = None,
) -> Block:
    """Build the block record from a token's source line map."""
    if token.map is None:
        raise ValueError(f"token {token.type} carries no source map")
    start_line, end_line = token.map
    char_start = line_offsets[start_line]
    char_end = line_offsets[end_line]
    raw = document_md[char_start:char_end].rstrip("\r\n")
    return Block(
        ordinal=ordinal,
        type=block_type,
        char_start=char_start,
        char_end=char_start + len(raw),
        block_hash=block_hash(raw=raw),
        heading_level=heading_level,
        heading_title=heading_title,
        normalized_title=(
            normalized_heading_title(title=heading_title)
            if heading_title is not None
            else None
        ),
    )


def _heading_text(*, token: Token) -> str:
    """Render a heading inline token to plain title text without re-tokenizing.

    The canonical markdown-it parse already produced child tokens. Reusing
    them preserves D57's single-tokenizer rule while removing emphasis/link
    markup from the title used by the parser, stats, and deterministic roles.
    """
    if token.children is None:
        return token.content.strip()
    pieces: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            pieces.append(child.content)
        elif child.type == "image":
            pieces.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            pieces.append(" ")
    return " ".join("".join(pieces).split())
