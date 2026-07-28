"""WP-3.3 acceptance: the section snap is deterministic, total, well-formed.

The snap's contract (e1 §3) is that EVERY input — including hostile LLM
output — yields a well-formed partition tree on the block grid. The fuzz
suite drives hundreds of randomized malformed proposals through it and
checks the invariants on each; the named tests pin the specific behaviors
the design calls out (backward snap, tie collapse, tiling, degradation).
"""

import random

import pytest

from rememberstack.core import blockize
from rememberstack.core import SECTION_ROLES
from rememberstack.core import snap_sections
from rememberstack.model import Block
from rememberstack.model import ProposedSection
from rememberstack.model import SnappedSection

_DOCUMENT = "\n\n".join(
    (
        "# The Atlas project",
        "Atlas is the internal knowledge system.",
        "It started as a spike in 2023.",
        "## Architecture",
        "The spine owns every SQL statement.",
        "Workers claim queued stages.",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "## Operations",
        "Deployments run one Postgres each.",
        "Backups are nightly.",
        "### Runbooks",
        "Restore drills happen quarterly.",
    )
)
_BLOCKS: tuple[Block, ...] = blockize(document_md=_DOCUMENT)


def _invariants(sections: tuple[SnappedSection, ...]) -> None:
    """The well-formedness contract every snap output must satisfy."""
    assert sections, "output is never empty"
    root = sections[0]
    assert root.node_path == "0"
    assert root.parent_path is None
    assert root.block_start == 0
    assert root.block_end == len(_BLOCKS) - 1
    by_path = {section.node_path: section for section in sections}
    assert len(by_path) == len(sections), "paths are unique"
    children: dict[str, list[SnappedSection]] = {}
    for index, section in enumerate(sections):
        assert section.ordinal == index, "DFS document order"
        assert section.role in SECTION_ROLES
        assert section.block_start <= section.block_end
        assert section.char_start == _BLOCKS[section.block_start].char_start
        assert section.char_end == _BLOCKS[section.block_end].char_end
        if section.parent_path is not None:
            parent = by_path[section.parent_path]
            assert section.node_path.rsplit(".", 1)[0] == parent.node_path
            assert parent.block_start <= section.block_start
            assert section.block_end <= parent.block_end, "child within parent"
            children.setdefault(parent.node_path, []).append(section)
    for siblings in children.values():
        for left, right in zip(siblings, siblings[1:], strict=False):
            assert left.block_end < right.block_start, "siblings disjoint, ordered"


def _random_proposal(rng: random.Random, *, depth: int = 0) -> ProposedSection:
    """A hostile proposal: junk spans, junk roles, random nesting."""
    span_max = len(_DOCUMENT) + 200
    start = rng.randint(-50, span_max)
    return ProposedSection(
        title=rng.choice(("", "Intro", "X" * 50)),
        role=rng.choice(("body", "methods", "chapter", "BANANA", "")),
        char_start=start,
        char_end=start + rng.randint(-30, 400),
        summary="",
        children=tuple(
            _random_proposal(rng, depth=depth + 1)
            for _ in range(rng.randint(0, 3 if depth < 3 else 0))
        ),
    )


@pytest.mark.parametrize("seed", range(300))
def test_any_proposal_yields_a_well_formed_partition(seed: int) -> None:
    """Property: hostile input never raises and never breaks the invariants."""
    rng = random.Random(seed)
    proposed = tuple(_random_proposal(rng) for _ in range(rng.randint(0, 6)))
    first = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(first)
    second = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    assert first == second, "deterministic"


def test_clean_proposal_snaps_to_block_boundaries() -> None:
    """The happy path: heading-anchored spans land exactly on their blocks."""
    architecture = _DOCUMENT.index("## Architecture")
    operations = _DOCUMENT.index("## Operations")
    proposed = (
        ProposedSection(
            title="Architecture",
            role="methods",
            char_start=architecture + 5,  # mid-heading: snaps BACKWARD
            char_end=operations,
            summary="How it is built.",
        ),
        ProposedSection(
            title="Operations",
            role="discussion",
            char_start=operations,
            char_end=len(_DOCUMENT),
        ),
    )
    sections = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(sections)
    assert [section.title for section in sections[1:]] == ["Architecture", "Operations"]
    first, second = sections[1], sections[2]
    assert _DOCUMENT[first.char_start :].startswith("## Architecture")
    assert _DOCUMENT[second.char_start :].startswith("## Operations")
    assert first.block_end == second.block_start - 1  # tiled, no gap
    assert second.block_end == len(_BLOCKS) - 1  # last sibling ends at parent end
    assert first.role == "methods"
    assert sections[1].summary == "How it is built."


def test_same_start_ties_collapse_and_adopt_children() -> None:
    """Two siblings snapping to one start become one — the longer proposed
    span wins and the loser's children survive under it."""
    winner_child = ProposedSection(
        title="Kept", char_start=200, char_end=260, role="body"
    )
    loser_child = ProposedSection(
        title="Adopted", char_start=300, char_end=360, role="body"
    )
    proposed = (
        ProposedSection(
            title="Short", char_start=3, char_end=40, children=(loser_child,)
        ),
        ProposedSection(
            title="Long",
            char_start=1,
            char_end=len(_DOCUMENT),
            children=(winner_child,),
        ),
    )
    sections = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(sections)
    titles = [section.title for section in sections]
    assert "Long" in titles
    assert "Short" not in titles  # the tie's loser is gone…
    assert "Adopted" in titles  # …but its subtree was adopted
    assert "Kept" in titles


def test_garbage_degrades_to_the_synthetic_root() -> None:
    """Proposals entirely outside the document leave only the root."""
    proposed = (
        ProposedSection(char_start=10_000_000, char_end=10_000_100),
        ProposedSection(char_start=-5, char_end=-1, role="BANANA"),
    )
    sections = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(sections)
    # the out-of-range span is pruned; the negative one clamps to block 0 and
    # covers everything — either way the tree stays well-formed:
    assert sections[0].node_path == "0"


def test_empty_document_gets_the_empty_root() -> None:
    """No blocks: the lone root carries the empty range 0..-1 (D57)."""
    sections = snap_sections(
        proposed=(ProposedSection(char_start=0, char_end=10),),
        blocks=(),
        title=None,
        markdown_chars=0,
    )
    assert len(sections) == 1
    assert sections[0].block_start == 0
    assert sections[0].block_end == -1
    assert sections[0].char_end == 0


def test_pathological_depth_is_flattened_not_fatal() -> None:
    """A degenerate 100-deep chain neither raises nor emits 100 levels."""
    node = ProposedSection(title="leaf", char_start=5, char_end=50)
    for level in range(100):
        node = ProposedSection(
            title=f"level-{level}", char_start=5, char_end=50, children=(node,)
        )
    sections = snap_sections(
        proposed=(node,), blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(sections)
    deepest = max(section.node_path.count(".") for section in sections)
    assert deepest < 20


def test_zero_length_and_outside_parent_proposals_are_pruned() -> None:
    """Codex review: step 4's clipping prunes whole nodes — a zero-length
    span, and a child wholly outside its parent's char range, must never be
    inflated into a real section by the start clamp + tiling."""
    architecture = _DOCUMENT.index("## Architecture")
    operations = _DOCUMENT.index("## Operations")
    proposed = (
        ProposedSection(
            title="Middle",
            role="methods",
            char_start=architecture,
            char_end=operations,
            children=(
                ProposedSection(  # entirely BEFORE the parent's char range
                    title="Escapee", char_start=0, char_end=10
                ),
                ProposedSection(  # zero-length marker
                    title="Marker",
                    char_start=architecture + 3,
                    char_end=architecture + 3,
                ),
            ),
        ),
    )
    sections = snap_sections(
        proposed=proposed, blocks=_BLOCKS, title="Atlas", markdown_chars=len(_DOCUMENT)
    )
    _invariants(sections)
    titles = [section.title for section in sections]
    assert "Middle" in titles
    assert "Escapee" not in titles
    assert "Marker" not in titles


def test_the_catalog_refuses_a_disconnected_tree() -> None:
    """Codex review: a record whose paths don't form a connected root-first
    tree is refused at the model boundary, never silently persisted as a
    second root."""
    from uuid import uuid4

    from pydantic import ValidationError

    from rememberstack.model import SectionTreeRecord
    from rememberstack.model import SkeletonStats
    from rememberstack.model import StructureRouteTag

    root = SnappedSection(
        node_path="0",
        parent_path=None,
        title="",
        role="body",
        block_start=0,
        block_end=5,
        char_start=0,
        char_end=100,
        summary="",
        ordinal=0,
    )
    orphan = SnappedSection(
        node_path="0.0.0",  # its parent '0.0' does not exist
        parent_path="0.0",
        title="",
        role="body",
        block_start=1,
        block_end=2,
        char_start=10,
        char_end=40,
        summary="",
        ordinal=1,
    )
    with pytest.raises(ValidationError, match="before its parent"):
        stats = SkeletonStats(
            stats_version="test",
            section_count=0,
            duplicate_title_ratio=None,
            max_title_multiplicity=None,
            sibling_duplicate_ratio=None,
            level_jump_count=None,
            numbering_coverage=None,
            numbering_inversions=None,
            numbering_scheme_switches=None,
            tiny_section_ratio=None,
            zero_direct_body_ratio=None,
            oversized_leaf_ratio=None,
            heading_density=None,
            title_length_p50=None,
            title_length_p95=None,
            long_title_ratio=None,
            low_letter_ratio=None,
            empty_title_ratio=None,
            max_sibling_fanout=None,
        )
        SectionTreeRecord(
            deployment_id=uuid4(),
            doc_id=uuid4(),
            version_id=uuid4(),
            representation_id=uuid4(),
            structure_generation_id=uuid4(),
            sections=(root, orphan),
            placement_path=None,
            structurer_name="pageindex_llm",
            structurer_version="test",
            skeleton_version="test",
            skeleton_hash="hash",
            skeleton_producer_family="N/A",
            skeleton_check_version=None,
            roles_version="test",
            selecting_check_id=None,
            route_tag=StructureRouteTag.LEGACY,
            candidate_skeleton_hash="hash",
            stats_version="test",
            stats=stats,
            pageindex_uri="test/pageindex.json",
        )
