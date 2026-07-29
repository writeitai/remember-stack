"""D79 section skeleton, checker, role, and immutable-generation values."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


class ProposedSection(BaseModel):
    """A span proposal accepted only by the generic D57 snap primitive.

    D79 removed this shape from the E0 model boundary. It remains an internal
    value for the total snap algorithm and its regression corpus; no provider
    response contains offsets anymore.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    title: str = ""
    role: str = "body"
    char_start: int = 0
    char_end: int = 0
    summary: str = ""
    children: tuple[ProposedSection, ...] = ()


class FallbackAnchor(BaseModel):
    """One fallback-proposed exact block-contained anchor.

    ``occurrence_index`` is zero-based among exact occurrences inside the
    enclosing parent's block range. Geometry is deliberately absent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor: str = Field(min_length=1)
    occurrence_index: int = Field(ge=0)
    children: tuple[FallbackAnchor, ...] = ()


class FallbackStructureResponse(BaseModel):
    """The D79 fallback seat's closed anchor-only response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[FallbackAnchor, ...] = ()


class SkeletonVerdict(StrEnum):
    """The checker can express exactly one primary skeleton-quality verdict."""

    COHERENT = "coherent"
    INCOHERENT_REPEATED_BOILERPLATE = "incoherent_repeated_boilerplate"
    INCOHERENT_HEADING_SEQUENCE = "incoherent_heading_sequence"
    INCOHERENT_JUNK_TITLES = "incoherent_junk_titles"
    INCOHERENT_OVER_FRAGMENTED = "incoherent_over_fragmented"


class SkeletonCheckResponse(BaseModel):
    """One closed enum and no explanation, confidence, or proposal fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Literal[
        "coherent",
        "incoherent_repeated_boilerplate",
        "incoherent_heading_sequence",
        "incoherent_junk_titles",
        "incoherent_over_fragmented",
    ]


class SkeletonCheckOutcome(StrEnum):
    """Persisted checker outcome: verdicts plus explicit non-verdict states."""

    NOT_RUN_SHORT = "not_run_short"
    COHERENT = "coherent"
    INCOHERENT_REPEATED_BOILERPLATE = "incoherent_repeated_boilerplate"
    INCOHERENT_HEADING_SEQUENCE = "incoherent_heading_sequence"
    INCOHERENT_JUNK_TITLES = "incoherent_junk_titles"
    INCOHERENT_OVER_FRAGMENTED = "incoherent_over_fragmented"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"


class StructureRouteTag(StrEnum):
    """Why one immutable skeleton generation has the shape it carries."""

    PARSER = "parser"
    PARSER_DEMOTED_CHECK = "parser_demoted_check"
    FALLBACK_DENSITY = "fallback_density"
    FALLBACK_LEAF = "fallback_leaf"
    FALLBACK_AFTER_CHECK = "fallback_after_check"
    SYNTHETIC_AFTER_CHECK = "synthetic_after_check"
    LEGACY = "legacy"


class SkeletonStats(BaseModel):
    """The exact versioned D79 sanity-check stat schema.

    ``section_count`` remains populated so eligibility is auditable. Every
    formula field is null when it is below ``MIN_CHECK_SECTIONS``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stats_version: str
    section_count: int = Field(ge=0)
    duplicate_title_ratio: float | None
    max_title_multiplicity: int | None
    sibling_duplicate_ratio: float | None
    level_jump_count: int | None
    numbering_coverage: float | None
    numbering_inversions: int | None
    numbering_scheme_switches: int | None
    tiny_section_ratio: float | None
    zero_direct_body_ratio: float | None
    oversized_leaf_ratio: float | None
    heading_density: float | None
    title_length_p50: float | None
    title_length_p95: float | None
    long_title_ratio: float | None
    low_letter_ratio: float | None
    empty_title_ratio: float | None
    max_sibling_fanout: int | None


class RoleAssignment(BaseModel):
    """One title-only classifier assignment keyed to an existing node path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_path: str = Field(min_length=1)
    role: Literal[
        "body",
        "abstract",
        "introduction",
        "results",
        "methods",
        "discussion",
        "conclusion",
        "references",
        "appendix",
        "table",
        "figure_caption",
        "nav",
        "boilerplate",
        "legal",
    ]


class RoleClassificationResponse(BaseModel):
    """The bounded title-only role seat's closed response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignments: tuple[RoleAssignment, ...] = ()


class SectionSummaryResponse(BaseModel):
    """One section summary and no auxiliary prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)


class RootSummaryPlacementResponse(BaseModel):
    """The root reduction's closed two-field summary + D39 placement shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    placement_path: str = Field(min_length=1)


class SnappedSection(BaseModel):
    """One well-formed section after the deterministic snap (block coordinates).

    ``block_end`` is inclusive; the empty document's root carries the empty
    range ``0..-1`` on the block grid (D57) with a zero-width char span.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_path: str  # materialized path, e.g. '0.2.1'; the root is '0'
    parent_path: str | None
    title: str
    role: str
    block_start: int = Field(ge=0)
    block_end: int = Field(ge=-1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    summary: str | None
    ordinal: int = Field(ge=0)
    heading_level: int | None = Field(default=None, ge=1, le=6)
    normalized_title: str = ""


class SkeletonCheckRecord(BaseModel):
    """One append-only D52 checker record; never stores completion text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: UUID
    processing_id: UUID
    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    candidate_skeleton_hash: str
    stats_version: str
    stats: SkeletonStats
    sampled_input_hash: str | None
    """Null when no prompt was ever rendered (not_run_short) — a hash of an
    unsent input would be bookkeeping fiction."""
    check_outcome: SkeletonCheckOutcome
    checker_component_version: str
    checker_model: str
    checker_model_hash: str
    checker_prompt_hash: str
    checker_schema_hash: str
    provider_failure: dict[str, str | int | float | bool | None] | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: Decimal | None
    latency_ms: int | None


class SectionTreeRecord(BaseModel):
    """The complete write input for one representation's section tree.

    ``sections`` is in depth-first document order with the root first — the
    catalog resolves each row's parent id from the paths as it inserts, so
    the record refuses any ordering or path structure that would silently
    persist a disconnected tree (an orphan row would reach E1 as a second
    root and double-chunk its range).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    structure_generation_id: UUID
    sections: tuple[SnappedSection, ...] = Field(min_length=1)
    placement_path: str | None
    structurer_name: str
    structurer_version: str
    skeleton_version: str
    skeleton_hash: str
    skeleton_producer_family: str
    skeleton_check_version: str | None
    roles_version: str | None
    summary_version: str | None = None
    placement_version: str | None = None
    selecting_check_id: UUID | None
    route_tag: StructureRouteTag
    candidate_skeleton_hash: str
    stats_version: str
    stats: SkeletonStats
    pageindex_uri: str
    make_current: bool = True

    @model_validator(mode="after")
    def _tree_is_connected(self) -> SectionTreeRecord:
        """Root first; every node extends a parent that appeared before it."""
        root = self.sections[0]
        if root.node_path != "0" or root.parent_path is not None:
            raise ValueError("the first section must be the root '0'")
        seen = {root.node_path}
        for section in self.sections[1:]:
            if section.node_path in seen:
                raise ValueError(f"duplicate section path {section.node_path!r}")
            if section.parent_path not in seen:
                raise ValueError(
                    f"section {section.node_path!r} appears before its parent"
                )
            if section.node_path.rsplit(".", 1)[0] != section.parent_path:
                raise ValueError(
                    f"section {section.node_path!r} does not extend its"
                    f" parent path {section.parent_path!r}"
                )
            seen.add(section.node_path)
        return self


class PersistedSectionTree(BaseModel):
    """What one representation's section-tree write actually landed.

    On a retried attempt the FIRST write wins row by row, so the caller must
    treat this — not its own input — as the truth (the sidecar is derived
    from it, never from a fresher LLM proposal).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[SnappedSection, ...] = Field(min_length=1)
    placement_path: str | None
    structurer_version: str
    structure_generation_id: UUID
    pageindex_uri: str
    skeleton_version: str
    skeleton_hash: str
    skeleton_producer_family: str
    skeleton_check_version: str | None
    roles_version: str | None
    summary_version: str | None
    placement_version: str | None
    selecting_check_id: UUID | None
    route_tag: StructureRouteTag
    candidate_skeleton_hash: str
    stats_version: str
    stats: SkeletonStats
