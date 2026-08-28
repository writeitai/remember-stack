"""ER cascade values (D17/D95): candidates, bands, verdicts, and T4.

Block-loose / decide-tight: T0/T1/T2 generate candidates and never accept;
T3 may accept one profiled candidate and T4 decides the residue. One global
threshold set is golden-set measured and versioned in `resolver_versions` —
never committed as an unmeasured constant.
"""

from enum import StrEnum
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

    T3 cosine >= accept may match a sole candidate. Production resolution
    sends every other score to T4; the reject band remains part of the
    registry-free golden-pair evaluator so regressions retain deciding-tier
    attribution. Blocking remains a recall ceiling, never a verdict.
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


class IdentityVerdict(StrEnum):
    """The three evidentiary identity outcomes produced by T4 (D99)."""

    SAME = "same"
    DIFFERENT = "different"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AdjudicationVerdict(BaseModel):
    """The T4 call's tri-state identity verdict with confidence and rationale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: IdentityVerdict
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    rationale: str | None = None
