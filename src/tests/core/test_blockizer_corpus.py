"""The blockizer golden corpus: locked hashes per BLOCKIZER_VERSION (WP-0.7, D57)."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

from markdown_it import __version__ as markdown_it_version

from rememberstack.core import block_hash
from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import ChunkerParams
from rememberstack.core import pack_blocks
from rememberstack.model import SectionSpan

_CORPUS = Path(__file__).resolve().parents[1] / "blockizer_corpus"


def test_seed_document_hash_sequence_is_locked() -> None:
    """A parser or normalization change trips this lock — drift is a version bump."""
    expected = json.loads((_CORPUS / "expected_hashes.json").read_text())
    source = (_CORPUS / "seed_mixed.md").read_text()

    assert expected["blockizer_version"] == BLOCKIZER_VERSION
    assert expected["parser"] == f"markdown-it-py=={markdown_it_version}"

    blocks = blockize(document_md=source)
    observed = [
        {"ordinal": block.ordinal, "type": block.type.value, "hash": block.block_hash}
        for block in blocks
    ]
    assert observed == expected["blocks"]


def test_heading_metadata_reuses_the_canonical_parse_and_normalizes_nfkc() -> None:
    """D79 metadata is raw-level + NFKC/casefold/whitespace title only."""
    source = "### ＦＯＯ   *Bär*\n\nBody.\n"
    heading, body = blockize(document_md=source)
    assert heading.heading_level == 3
    assert heading.heading_title == "ＦＯＯ Bär"
    assert heading.normalized_title == "foo bär"
    assert body.heading_level is None
    assert body.normalized_title is None


def test_chunk_work_identity_tracks_the_metadata_only_blockizer_bump() -> None:
    """The version bumps while the golden document's packed grid bytes stay locked."""
    from rememberstack.core import CHUNKER_VERSION
    from rememberstack.workers import E1_CHUNK_VERSION

    assert E1_CHUNK_VERSION == CHUNKER_VERSION
    source = (_CORPUS / "seed_mixed.md").read_text()
    blocks = blockize(document_md=source)
    chunks = pack_blocks(
        blocks=blocks,
        sections=(
            SectionSpan(
                section_id=UUID("00000000-0000-0000-0000-000000000079"),
                node_path="0",
                role="body",
                block_start=0,
                block_end=len(blocks) - 1,
            ),
        ),
        document_md=source,
        params=ChunkerParams(
            token_budget=20, anchor_modulus=24, anchor_min_gap_tokens=200
        ),
    )
    grid_bytes = json.dumps(
        [chunk.model_dump(mode="json") for chunk in chunks],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(grid_bytes).hexdigest() == (
        "a8f22df40a4492ca66bc87b7b744d508c9dbcd72e386548bded0040b37f402ff"
    )


def test_offsets_slice_the_source_exactly() -> None:
    """Every block's offsets are a real slice of document.md (the grounding chain)."""
    source = (_CORPUS / "seed_mixed.md").read_text()
    for block in blockize(document_md=source):
        raw = source[block.char_start : block.char_end]
        assert raw
        assert block_hash(raw=raw) == block.block_hash


def test_reflow_does_not_change_identity() -> None:
    """A pure hard-wrap change never changes a block's hash (D56 reuse depends on it)."""
    wrapped = "One sentence split\nacross three\nsource lines.\n"
    reflowed = "One sentence split across three source lines.\n"
    (wrapped_block,) = blockize(document_md=wrapped)
    (reflowed_block,) = blockize(document_md=reflowed)
    assert wrapped_block.block_hash == reflowed_block.block_hash


def test_edit_changes_exactly_the_edited_block() -> None:
    """Editing one paragraph changes only its hash — the D56 edit-locality property."""
    original = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"
    edited = "First paragraph.\n\nSecond paragraph, edited.\n\nThird paragraph.\n"
    before = [block.block_hash for block in blockize(document_md=original)]
    after = [block.block_hash for block in blockize(document_md=edited)]
    assert before[0] == after[0]
    assert before[1] != after[1]
    assert before[2] == after[2]


def test_crlf_and_lf_documents_hash_identically() -> None:
    """Codex review 6: line-ending style never changes identity or leaves residue."""
    lf = "# Title\n\nBody line one\nline two.\n"
    crlf = lf.replace("\n", "\r\n")
    lf_blocks = blockize(document_md=lf)
    crlf_blocks = blockize(document_md=crlf)
    assert [b.block_hash for b in lf_blocks] == [b.block_hash for b in crlf_blocks]
    for block in crlf_blocks:
        assert not crlf[block.char_start : block.char_end].endswith("\r")


def test_nested_list_items_stay_inside_their_parent_block() -> None:
    """Locked choice (Codex review 5 overruled): top-level items are atomic —
    emitting nested items separately would create overlapping spans, and chunks
    require non-overlapping whole-block runs (e1 §4). Content is preserved."""
    source = "- parent item\n  - nested child\n- sibling\n"
    blocks = blockize(document_md=source)
    assert [block.type.value for block in blocks] == ["list_item", "list_item"]
    parent_raw = source[blocks[0].char_start : blocks[0].char_end]
    assert "nested child" in parent_raw
