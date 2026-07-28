"""D79 deterministic skeleton parsing, fallback-anchor disposal, and stats."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Final
import unicodedata

from rememberstack.core.blockizer import normalized_heading_title
from rememberstack.model import Block
from rememberstack.model import BlockType
from rememberstack.model import FallbackAnchor
from rememberstack.model import SkeletonStats
from rememberstack.model import SnappedSection

MIN_CHECK_SECTIONS: Final = 3
"""Minimum non-root section count for a D79 checker call."""

TINY_FLOOR: Final = 80
"""Direct-body characters below this count as tiny in stats version 1."""

LONG_TITLE: Final = 120
"""Normalized-title length reported as long and the checker line cap."""

MAX_FALLBACK_DEPTH: Final = 16
"""Preserves the D57 snap's pathological-recursion ceiling."""

SKELETON_STATS_VERSION: Final = "e0-skeleton-stats-2026.07:d79-v1:min3-tiny80-long120"
"""Pins every formula, zero case, normalization, and named floor below."""

SKELETON_PARSER_VERSION: Final = (
    "e0-skeleton-parser-2026.07:d79-heading-stack:block-grid-v1"
)
"""The deterministic heading-stack skeleton producer generation."""

_ARABIC_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)(?=$|[\s.)])")
_ROMAN_NUMBER = re.compile(r"^([IVXLCDM]+)(?=$|[\s.)])", re.IGNORECASE)
_ALPHA_NUMBER = re.compile(r"^([A-Z])\.(?=$|\s)", re.IGNORECASE)


@dataclass(frozen=True)
class SkeletonAnalysis:
    """Stats plus the shared unmasked measurements used by routing/rendering."""

    stats: SkeletonStats
    direct_body_chars: dict[str, int]
    leaf_span_chars: dict[str, int]
    heading_density: float
    oversized_leaf_ratio: float


@dataclass
class _HeadingNode:
    """Mutable construction node; persistence receives frozen sections."""

    block: Block
    children: list[_HeadingNode]
    block_end: int = -1


@dataclass
class _AnchorNode:
    """One resolved fallback anchor on the block grid."""

    proposal: FallbackAnchor
    block_start: int
    block_end: int
    children: list[_AnchorNode]


def parse_heading_skeleton(
    *, blocks: tuple[Block, ...], title: str | None, markdown_chars: int
) -> tuple[SnappedSection, ...]:
    """Build a deterministic tree from canonical heading blocks.

    A heading's parent is the nearest preceding heading with a strictly lower
    raw markdown level. Skipped levels therefore add one materialized edge,
    not placeholder sections. Each span begins at its heading block and ends
    immediately before the next heading at the same or a lower level.
    """
    root = _root_section(blocks=blocks, title=title, markdown_chars=markdown_chars)
    headings = tuple(block for block in blocks if block.type is BlockType.HEADING)
    if not headings:
        return (root,)

    roots: list[_HeadingNode] = []
    stack: list[_HeadingNode] = []
    nodes: list[_HeadingNode] = []
    for heading in headings:
        level = _required_heading_level(block=heading)
        while stack and _required_heading_level(block=stack[-1].block) >= level:
            stack.pop()
        node = _HeadingNode(block=heading, children=[])
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
        nodes.append(node)

    last_block = len(blocks) - 1
    for index, node in enumerate(nodes):
        level = _required_heading_level(block=node.block)
        node.block_end = last_block
        for following in nodes[index + 1 :]:
            if _required_heading_level(block=following.block) <= level:
                node.block_end = following.block.ordinal - 1
                break

    output = [root]
    _materialize_heading_nodes(
        nodes=roots, parent_path="0", blocks=blocks, output=output
    )
    return tuple(output)


def resolve_fallback_skeleton(
    *,
    proposed: tuple[FallbackAnchor, ...],
    blocks: tuple[Block, ...],
    document_md: str,
    title: str | None,
) -> tuple[SnappedSection, ...]:
    """Resolve exact anchors and deterministically partition on the block grid.

    Missing anchors disappear and their children are retried in the enclosing
    parent. Duplicate text is resolved by ``occurrence_index``. Siblings are
    ordered by their resolved block and tile to the next sibling; same-block
    siblings collapse with the first proposal adopting later children.
    """
    root = _root_section(blocks=blocks, title=title, markdown_chars=len(document_md))
    if not blocks or not proposed:
        return (root,)
    nodes = _resolve_anchor_level(
        proposed=proposed,
        parent_start=0,
        parent_end=len(blocks) - 1,
        blocks=blocks,
        document_md=document_md,
        depth=1,
    )
    output = [root]
    _materialize_anchor_nodes(
        nodes=nodes, parent_path="0", blocks=blocks, output=output
    )
    return tuple(output)


def analyze_skeleton(
    *,
    sections: tuple[SnappedSection, ...],
    blocks: tuple[Block, ...],
    markdown_chars: int,
) -> SkeletonAnalysis:
    """Compute the normative D79 stats and shared route-gate measurements."""
    candidates = sections[1:]
    count = len(candidates)
    children = _children_by_parent(sections=candidates)
    direct_body = {
        section.node_path: _direct_body_size(
            section=section, children=children.get(section.node_path, ()), blocks=blocks
        )
        for section in candidates
    }
    leaf_spans = {
        section.node_path: section.char_end - section.char_start
        for section in candidates
        if not children.get(section.node_path)
    }
    heading_density = _safe_ratio(count * 10_000, markdown_chars)
    oversized_leaf_ratio = _safe_ratio(
        max(leaf_spans.values(), default=0), markdown_chars
    )

    if count < MIN_CHECK_SECTIONS:
        stats = SkeletonStats(
            stats_version=SKELETON_STATS_VERSION,
            section_count=count,
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
        return SkeletonAnalysis(
            stats=stats,
            direct_body_chars=direct_body,
            leaf_span_chars=leaf_spans,
            heading_density=heading_density,
            oversized_leaf_ratio=oversized_leaf_ratio,
        )

    titles = tuple(section.normalized_title for section in candidates)
    multiplicities = Counter(titles)
    sibling_duplicates = sum(
        len(siblings) - len({section.normalized_title for section in siblings})
        for siblings in children.values()
    )
    levels = tuple(_stat_level(section=section) for section in candidates)
    numbering = tuple(_leading_number(title=title) for title in titles)
    inversions, switches = _numbering_run_stats(
        children=children,
        numbering_by_path=dict(
            zip((section.node_path for section in candidates), numbering, strict=True)
        ),
    )
    lengths = tuple(len(title) for title in titles)
    stats = SkeletonStats(
        stats_version=SKELETON_STATS_VERSION,
        section_count=count,
        duplicate_title_ratio=1.0 - (len(multiplicities) / count),
        max_title_multiplicity=max(multiplicities.values(), default=0),
        sibling_duplicate_ratio=sibling_duplicates / count,
        level_jump_count=sum(
            max(0, right - left - 1)
            for left, right in zip(levels, levels[1:], strict=False)
        ),
        numbering_coverage=sum(item is not None for item in numbering) / count,
        numbering_inversions=inversions,
        numbering_scheme_switches=switches,
        tiny_section_ratio=sum(size < TINY_FLOOR for size in direct_body.values())
        / count,
        zero_direct_body_ratio=sum(size == 0 for size in direct_body.values()) / count,
        oversized_leaf_ratio=oversized_leaf_ratio,
        heading_density=heading_density,
        title_length_p50=_nearest_rank(values=lengths, percentile=0.50),
        title_length_p95=_nearest_rank(values=lengths, percentile=0.95),
        long_title_ratio=sum(length > LONG_TITLE for length in lengths) / count,
        low_letter_ratio=sum(_letter_ratio(title=title) < 0.5 for title in titles)
        / count,
        empty_title_ratio=sum(not title for title in titles) / count,
        max_sibling_fanout=max(
            (len(siblings) for siblings in children.values()), default=0
        ),
    )
    return SkeletonAnalysis(
        stats=stats,
        direct_body_chars=direct_body,
        leaf_span_chars=leaf_spans,
        heading_density=heading_density,
        oversized_leaf_ratio=oversized_leaf_ratio,
    )


def skeleton_hash(*, sections: tuple[SnappedSection, ...]) -> str:
    """Hash skeleton geometry/title metadata, deliberately excluding roles."""
    payload = [
        {
            "node_path": section.node_path,
            "parent_path": section.parent_path,
            "title": section.title,
            "normalized_title": section.normalized_title,
            "heading_level": section.heading_level,
            "block_start": section.block_start,
            "block_end": section.block_end,
            "char_start": section.char_start,
            "char_end": section.char_end,
        }
        for section in sections
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deterministic_section_role(*, normalized_title: str) -> str | None:
    """Resolve unambiguous title-only roles before the bounded classifier."""
    exact = {
        "abstract": "abstract",
        "summary": "abstract",
        "introduction": "introduction",
        "background": "introduction",
        "methods": "methods",
        "methodology": "methods",
        "materials and methods": "methods",
        "results": "results",
        "findings": "results",
        "discussion": "discussion",
        "conclusion": "conclusion",
        "conclusions": "conclusion",
        "references": "references",
        "bibliography": "references",
        "works cited": "references",
        "table of contents": "nav",
        "contents": "nav",
        "navigation": "nav",
        "tables": "table",
        "figures": "figure_caption",
        "figure captions": "figure_caption",
        "legal": "legal",
        "legal notice": "legal",
        "terms and conditions": "legal",
        "boilerplate": "boilerplate",
        # Media wrapper headings have fixed, explicit roles.
        "transcript": "body",
        "audio transcript": "body",
        "video transcript": "body",
        "acoustic events": "body",
        "visible text (ocr)": "body",
        "shot notes": "body",
        "image description": "figure_caption",
        "visual description": "figure_caption",
    }
    if normalized_title in exact:
        return exact[normalized_title]
    if normalized_title == "appendix" or normalized_title.startswith("appendix "):
        return "appendix"
    if normalized_title == "table" or normalized_title.startswith("table "):
        return "table"
    if normalized_title == "figure" or normalized_title.startswith("figure "):
        return "figure_caption"
    return None


def _root_section(
    *, blocks: tuple[Block, ...], title: str | None, markdown_chars: int
) -> SnappedSection:
    return SnappedSection(
        node_path="0",
        parent_path=None,
        title=title or "",
        role="body",
        block_start=0,
        block_end=len(blocks) - 1,
        char_start=0,
        char_end=markdown_chars,
        summary="",
        ordinal=0,
        heading_level=None,
        normalized_title=normalized_heading_title(title=title or ""),
    )


def _materialize_heading_nodes(
    *,
    nodes: Iterable[_HeadingNode],
    parent_path: str,
    blocks: tuple[Block, ...],
    output: list[SnappedSection],
) -> None:
    for sibling_index, node in enumerate(nodes):
        path = f"{parent_path}.{sibling_index}"
        block = node.block
        output.append(
            SnappedSection(
                node_path=path,
                parent_path=parent_path,
                title=block.heading_title or "",
                role="body",
                block_start=block.ordinal,
                block_end=node.block_end,
                char_start=block.char_start,
                char_end=blocks[node.block_end].char_end,
                summary="",
                ordinal=len(output),
                heading_level=_required_heading_level(block=block),
                normalized_title=block.normalized_title or "",
            )
        )
        _materialize_heading_nodes(
            nodes=node.children, parent_path=path, blocks=blocks, output=output
        )


def _resolve_anchor_level(
    *,
    proposed: tuple[FallbackAnchor, ...],
    parent_start: int,
    parent_end: int,
    blocks: tuple[Block, ...],
    document_md: str,
    depth: int,
) -> list[_AnchorNode]:
    if depth >= MAX_FALLBACK_DEPTH:
        return []
    candidates: list[tuple[int, int, FallbackAnchor]] = []
    emission_index = 0

    def collect(items: tuple[FallbackAnchor, ...]) -> None:
        nonlocal emission_index
        for item in items:
            current_emission = emission_index
            emission_index += 1
            resolved = _resolve_anchor(
                anchor=item.anchor,
                occurrence_index=item.occurrence_index,
                parent_start=parent_start,
                parent_end=parent_end,
                blocks=blocks,
                document_md=document_md,
            )
            if resolved is None:
                collect(item.children)
            else:
                candidates.append((resolved, current_emission, item))

    collect(proposed)
    candidates.sort(key=lambda item: item[:2])
    deduped: list[tuple[int, FallbackAnchor]] = []
    for start, _, proposal in candidates:
        if deduped and deduped[-1][0] == start:
            prior_start, prior = deduped[-1]
            deduped[-1] = (
                prior_start,
                prior.model_copy(
                    update={"children": (*prior.children, *proposal.children)}
                ),
            )
        else:
            deduped.append((start, proposal))

    result: list[_AnchorNode] = []
    for index, (start, proposal) in enumerate(deduped):
        end = deduped[index + 1][0] - 1 if index + 1 < len(deduped) else parent_end
        if end < start:
            continue
        children = _resolve_anchor_level(
            proposed=proposal.children,
            parent_start=start,
            parent_end=end,
            blocks=blocks,
            document_md=document_md,
            depth=depth + 1,
        )
        result.append(
            _AnchorNode(
                proposal=proposal, block_start=start, block_end=end, children=children
            )
        )
    return result


def _resolve_anchor(
    *,
    anchor: str,
    occurrence_index: int,
    parent_start: int,
    parent_end: int,
    blocks: tuple[Block, ...],
    document_md: str,
) -> int | None:
    occurrences: list[int] = []
    for ordinal in range(parent_start, parent_end + 1):
        block = blocks[ordinal]
        raw = document_md[block.char_start : block.char_end]
        cursor = 0
        while True:
            found = raw.find(anchor, cursor)
            if found < 0:
                break
            occurrences.append(ordinal)
            cursor = found + max(1, len(anchor))
    if occurrence_index >= len(occurrences):
        return None
    return occurrences[occurrence_index]


def _materialize_anchor_nodes(
    *,
    nodes: Iterable[_AnchorNode],
    parent_path: str,
    blocks: tuple[Block, ...],
    output: list[SnappedSection],
) -> None:
    for sibling_index, node in enumerate(nodes):
        path = f"{parent_path}.{sibling_index}"
        normalized = normalized_heading_title(title=node.proposal.anchor)
        anchor_block = blocks[node.block_start]
        output.append(
            SnappedSection(
                node_path=path,
                parent_path=parent_path,
                title=node.proposal.anchor,
                role="body",
                block_start=node.block_start,
                block_end=node.block_end,
                char_start=anchor_block.char_start,
                char_end=blocks[node.block_end].char_end,
                summary="",
                ordinal=len(output),
                heading_level=(
                    anchor_block.heading_level
                    if anchor_block.type is BlockType.HEADING
                    else None
                ),
                normalized_title=normalized,
            )
        )
        _materialize_anchor_nodes(
            nodes=node.children, parent_path=path, blocks=blocks, output=output
        )


def _children_by_parent(
    *, sections: tuple[SnappedSection, ...]
) -> dict[str, tuple[SnappedSection, ...]]:
    grouped: defaultdict[str, list[SnappedSection]] = defaultdict(list)
    for section in sections:
        if section.parent_path is not None:
            grouped[section.parent_path].append(section)
    return {
        parent: tuple(sorted(children, key=lambda item: item.block_start))
        for parent, children in grouped.items()
    }


def _direct_body_size(
    *,
    section: SnappedSection,
    children: tuple[SnappedSection, ...],
    blocks: tuple[Block, ...],
) -> int:
    excluded = {section.block_start}
    for child in children:
        excluded.update(range(child.block_start, child.block_end + 1))
    return sum(
        blocks[ordinal].char_end - blocks[ordinal].char_start
        for ordinal in range(section.block_start, section.block_end + 1)
        if ordinal not in excluded and 0 <= ordinal < len(blocks)
    )


def _numbering_run_stats(
    *,
    children: dict[str, tuple[SnappedSection, ...]],
    numbering_by_path: dict[str, tuple[str, tuple[int, ...]] | None],
) -> tuple[int, int]:
    inversions = 0
    switches = 0
    for siblings in children.values():
        previous: tuple[str, tuple[int, ...]] | None = None
        for sibling in siblings:
            current = numbering_by_path[sibling.node_path]
            if current is None:
                previous = None
                continue
            if previous is not None:
                if previous[0] == current[0]:
                    if current[1] < previous[1]:
                        inversions += 1
                else:
                    switches += 1
            previous = current
    return inversions, switches


def _leading_number(title: str) -> tuple[str, tuple[int, ...]] | None:
    stripped = title.lstrip()
    arabic = _ARABIC_NUMBER.match(stripped)
    if arabic is not None:
        return "arabic_dotted", tuple(int(part) for part in arabic.group(1).split("."))
    alpha = _ALPHA_NUMBER.match(stripped)
    if alpha is not None and alpha.group(1).casefold() not in {
        "i",
        "v",
        "x",
        "l",
        "c",
        "d",
        "m",
    }:
        return "alpha", (ord(alpha.group(1).casefold()) - ord("a") + 1,)
    roman = _ROMAN_NUMBER.match(stripped)
    if roman is not None:
        value = _roman_value(token=roman.group(1).upper())
        if value is not None:
            return "roman", (value,)
    if alpha is not None:
        return "alpha", (ord(alpha.group(1).casefold()) - ord("a") + 1,)
    return None


def _roman_value(*, token: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for character in reversed(token):
        value = values[character]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    canonical = _to_roman(value=total)
    return total if canonical == token else None


def _to_roman(*, value: int) -> str:
    parts: list[str] = []
    for number, symbol in (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        while value >= number:
            parts.append(symbol)
            value -= number
    return "".join(parts)


def _stat_level(*, section: SnappedSection) -> int:
    return section.heading_level or min(section.node_path.count("."), 6)


def _nearest_rank(*, values: tuple[int, ...], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[index])


def _letter_ratio(*, title: str) -> float:
    if not title:
        return 0.0
    letters = sum(
        unicodedata.category(character).startswith("L") for character in title
    )
    return letters / len(title)


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _required_heading_level(*, block: Block) -> int:
    if block.heading_level is None:
        raise ValueError(f"heading block {block.ordinal} has no raw level")
    return block.heading_level
