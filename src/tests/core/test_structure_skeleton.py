"""D79 parser, fallback-anchor, and normative-stat contract proofs."""

from math import isclose

from rememberstack.core import analyze_skeleton
from rememberstack.core import blockize
from rememberstack.core import MIN_CHECK_SECTIONS
from rememberstack.core import parse_heading_skeleton
from rememberstack.core import resolve_fallback_skeleton
from rememberstack.model import FallbackAnchor


def _parse(source: str):
    blocks = blockize(document_md=source)
    sections = parse_heading_skeleton(
        blocks=blocks, title="Document", markdown_chars=len(source)
    )
    return blocks, sections


def test_nested_flat_and_skipped_heading_levels_assign_parents() -> None:
    """Raw levels select the nearest prior lower level; no placeholders exist."""
    source = "\n\n".join(
        (
            "# One",
            "body",
            "### Skipped child",
            "body",
            "## Sibling child",
            "body",
            "# Two",
            "body",
        )
    )
    _, sections = _parse(source)

    assert [(s.title, s.parent_path, s.heading_level) for s in sections[1:]] == [
        ("One", "0", 1),
        ("Skipped child", "0.0", 3),
        ("Sibling child", "0.0", 2),
        ("Two", "0", 1),
    ]
    one, skipped, sibling, two = sections[1:]
    assert one.block_end == two.block_start - 1
    assert skipped.block_end == sibling.block_start - 1

    flat = _parse("# A\n\nbody\n\n# B\n\nbody\n\n# C\n")[1]
    assert [section.parent_path for section in flat[1:]] == ["0", "0", "0"]


def test_no_headings_and_heading_only_documents_are_total() -> None:
    no_heading_blocks, no_heading = _parse("A paragraph.\n\nAnother paragraph.\n")
    assert len(no_heading) == 1
    assert no_heading[0].block_end == len(no_heading_blocks) - 1

    heading_blocks, heading_only = _parse("# A\n\n## B\n\n### C\n")
    assert [section.title for section in heading_only] == ["Document", "A", "B", "C"]
    assert heading_only[-1].block_start == heading_only[-1].block_end
    assert heading_only[0].block_end == len(heading_blocks) - 1


def test_duplicate_titles_are_preserved_as_distinct_heading_occurrences() -> None:
    _, sections = _parse("# Report\n\n## Summary\n\n## Summary\n")
    summaries = [section for section in sections if section.title == "Summary"]
    assert len(summaries) == 2
    assert summaries[0].node_path != summaries[1].node_path
    assert summaries[0].block_end < summaries[1].block_start


def test_five_heading_design_review_example_uses_raw_levels_not_tree_depth() -> None:
    """The five-heading review case includes both a skip and a level reset."""
    source = "\n\n".join(("## A", "#### A.1", "### A.2", "# B", "### B.1"))
    _, sections = _parse(source)
    assert [(s.title, s.parent_path) for s in sections[1:]] == [
        ("A", "0"),
        ("A.1", "0.0"),
        ("A.2", "0.0"),
        ("B", "0"),
        ("B.1", "0.1"),
    ]
    analysis = analyze_skeleton(
        sections=sections,
        blocks=blockize(document_md=source),
        markdown_chars=len(source),
    )
    assert analysis.stats.level_jump_count == 2  # 2→4 contributes 1; 1→3 contributes 1


def test_stats_duplicate_and_sibling_formulas_are_hand_computed() -> None:
    source = "# Chapter\n\n## Summary\n\n## Summary\n\n# Summary\n"
    blocks, sections = _parse(source)
    stats = analyze_skeleton(
        sections=sections, blocks=blocks, markdown_chars=len(source)
    ).stats

    assert stats.section_count == 4
    assert stats.duplicate_title_ratio == 0.5  # 1 - 2 distinct / 4
    assert stats.max_title_multiplicity == 3
    assert stats.sibling_duplicate_ratio == 0.25  # one duplicate / four headings
    assert stats.max_sibling_fanout == 2


def test_stats_numbering_runs_jumps_density_leaf_and_body_sizes() -> None:
    source = "\n\n".join(
        (
            "# 1. Chapter",
            "A" * 100,
            "### 3. Item",
            "B" * 10,
            "## 2. Item",
            "### A. Alpha",
            "C" * 90,
            "### B. Beta",
        )
    )
    blocks, sections = _parse(source)
    analysis = analyze_skeleton(
        sections=sections, blocks=blocks, markdown_chars=len(source)
    )
    stats = analysis.stats

    assert stats.section_count == 5
    assert stats.level_jump_count == 1  # raw 1→3 contributes one skipped level
    assert stats.numbering_coverage == 1.0
    # The parser makes raw H3/H2 peers under H1, so 3→2 is one real inversion.
    assert stats.numbering_inversions == 1
    # A/B are one alpha run; the separate arabic runs never switch in-place.
    assert stats.numbering_scheme_switches == 0
    assert isclose(stats.heading_density or 0, 50_000 / len(source))
    expected_leaf = max(
        section.char_end - section.char_start
        for section in sections[1:]
        if not any(child.parent_path == section.node_path for child in sections[1:])
    )
    assert isclose(stats.oversized_leaf_ratio or 0, expected_leaf / len(source))
    assert analysis.direct_body_chars["0.0"] == 100
    assert analysis.direct_body_chars["0.0.0"] == 10
    assert analysis.direct_body_chars["0.0.1.0"] == 90
    assert stats.tiny_section_ratio == 3 / 5
    assert stats.zero_direct_body_ratio == 2 / 5


def test_numbering_inversion_and_scheme_switch_count_same_parent_runs_only() -> None:
    source = "# Root\n\n## 3. Third\n\n## 2. Second\n\n## A. Alpha\n\n## B. Beta\n"
    blocks, sections = _parse(source)
    stats = analyze_skeleton(
        sections=sections, blocks=blocks, markdown_chars=len(source)
    ).stats
    assert stats.numbering_coverage == 4 / 5  # unnumbered Root
    assert stats.numbering_inversions == 1
    assert stats.numbering_scheme_switches == 1


def test_title_shape_nearest_rank_and_character_formulas() -> None:
    long = "A" * 121
    source = f"# {long}\n\n## 123\n\n##\n"
    blocks, sections = _parse(source)
    stats = analyze_skeleton(
        sections=sections, blocks=blocks, markdown_chars=len(source)
    ).stats
    assert stats.title_length_p50 == 3.0
    assert stats.title_length_p95 == 121.0
    assert stats.long_title_ratio == 1 / 3
    assert stats.low_letter_ratio == 2 / 3
    assert stats.empty_title_ratio == 1 / 3


def test_short_and_empty_stats_have_exact_null_zero_cases() -> None:
    for source in ("", "plain body", "# Only\n\nbody"):
        blocks, sections = _parse(source)
        analysis = analyze_skeleton(
            sections=sections, blocks=blocks, markdown_chars=len(source)
        )
        dumped = analysis.stats.model_dump()
        assert dumped["section_count"] < MIN_CHECK_SECTIONS
        assert all(
            value is None
            for key, value in dumped.items()
            if key not in {"stats_version", "section_count"}
        )
        if not source:
            assert analysis.heading_density == 0
            assert analysis.oversized_leaf_ratio == 0


def test_fallback_occurrence_index_resolves_duplicates() -> None:
    source = "Intro marker\n\nRepeat marker\n\nRepeat marker\n\nEnd marker\n"
    blocks = blockize(document_md=source)
    sections = resolve_fallback_skeleton(
        proposed=(
            FallbackAnchor(anchor="Repeat marker", occurrence_index=1, children=()),
        ),
        blocks=blocks,
        document_md=source,
        title="Fallback",
    )
    assert len(sections) == 2
    assert sections[1].block_start == 2


def test_unresolved_fallback_anchor_degrades_children_to_enclosing_parent() -> None:
    source = "Intro marker\n\nMiddle marker\n\nEnd marker\n"
    blocks = blockize(document_md=source)
    sections = resolve_fallback_skeleton(
        proposed=(
            FallbackAnchor(
                anchor="missing",
                occurrence_index=0,
                children=(
                    FallbackAnchor(
                        anchor="End marker", occurrence_index=0, children=()
                    ),
                ),
            ),
        ),
        blocks=blocks,
        document_md=source,
        title="Fallback",
    )
    assert [(section.title, section.parent_path) for section in sections[1:]] == [
        ("End marker", "0")
    ]


def test_same_block_fallback_boundaries_adopt_later_children() -> None:
    """D57 tie collapse keeps one boundary without erasing the later subtree."""
    source = "Alpha and Beta\n\nChild marker\n\nEnd marker\n"
    blocks = blockize(document_md=source)
    sections = resolve_fallback_skeleton(
        proposed=(
            FallbackAnchor(anchor="Alpha", occurrence_index=0, children=()),
            FallbackAnchor(
                anchor="Beta",
                occurrence_index=0,
                children=(
                    FallbackAnchor(
                        anchor="Child marker", occurrence_index=0, children=()
                    ),
                ),
            ),
        ),
        blocks=blocks,
        document_md=source,
        title="Fallback",
    )
    assert [(section.title, section.parent_path) for section in sections[1:]] == [
        ("Alpha", "0"),
        ("Child marker", "0.0"),
    ]


def test_fallback_nesting_preserves_the_snap_depth_ceiling() -> None:
    source = "\n\n".join(f"Marker {index}" for index in range(20))
    blocks = blockize(document_md=source)
    proposal = FallbackAnchor(anchor="Marker 19", occurrence_index=0, children=())
    for index in range(18, -1, -1):
        proposal = FallbackAnchor(
            anchor=f"Marker {index}", occurrence_index=0, children=(proposal,)
        )
    sections = resolve_fallback_skeleton(
        proposed=(proposal,), blocks=blocks, document_md=source, title="Deep fallback"
    )
    assert len(sections) == 16  # root plus the D57 maximum of 15 nested nodes
    assert sections[-1].node_path.count(".") == 15
