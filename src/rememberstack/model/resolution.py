"""ER cascade values (D17): candidates, bands, verdicts, and the T4 response.

Block-loose / decide-tight: T1/T2 generate candidates and never decide; T0,
T3, and T4 decide. One global threshold set is golden-set measured and
versioned in `resolver_versions` — never committed as an unmeasured constant.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_NonEmpty = Annotated[str, Field(min_length=1)]
_Unit = Annotated[float, Field(ge=-1.0, le=1.0)]


class ResolutionCandidate(BaseModel):
    """One blocked candidate: which tier surfaced it and its scores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    canonical_name: _NonEmpty
    blocking_tier: _NonEmpty  # T0 | T1 | T2
    trigram_score: float | None = None
    embedding_score: _Unit | None = None
    profile_summary: str | None = None
    salient_facts: tuple[str, ...] = ()


class ResolutionThresholds(BaseModel):
    """The global decision bands (starting points to measure, D22/D96).

    T3 cosine >= accept: match. <= reject: not this candidate. Between the
    bands: escalate to T4 — cheap tiers never auto-reject near-misses (the
    blocking ceiling is a recall ceiling, not a verdict).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    t3_accept: _Unit = 0.88
    t3_reject: _Unit = 0.60
    t4_small_confidence_floor: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75


class ResolverConfig(BaseModel):
    """The versioned cascade configuration (`resolver_versions` row shape)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver_version: _NonEmpty
    trigram_floor: Annotated[float, Field(ge=0.0, le=1.0)] = 0.3
    blocking_limit: Annotated[int, Field(ge=1)] = 10
    t4_max_candidates: Annotated[int, Field(ge=1)] = 3
    thresholds: ResolutionThresholds = ResolutionThresholds()


class AdjudicationVerdict(BaseModel):
    """The T4 call's structured output: same entity or not, with confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match: bool
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    rationale: str | None = None


class P1EntityRow(BaseModel):
    """One row of the P1 entities table: the T3 profile embedding home (D8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    deployment_id: UUID
    canonical_name: _NonEmpty
    profile_summary: _NonEmpty
    salient_facts: Annotated[tuple[_NonEmpty, ...], Field(min_length=1)]
    vector: Annotated[tuple[float, ...], Field(min_length=1)]
