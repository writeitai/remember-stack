"""ER cascade values (D17/D95/D100): candidates, bands, and binary T4.

Block-loose / decide-tight: T0/T1/T2 generate candidates and never accept;
T3 may accept one profiled candidate and T4 decides the residue. One global
threshold set is golden-set measured and versioned in `resolver_versions` —
never committed as an unmeasured constant.
"""

from typing import Annotated
from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

_NonEmpty = Annotated[str, Field(min_length=1)]
_Unit = Annotated[float, Field(ge=-1.0, le=1.0)]


class ResolutionCandidate(BaseModel):
    """One blocked candidate: which tier surfaced it and its scores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    canonical_name: _NonEmpty
    blocking_tier: _NonEmpty  # T0 | T1 | T2
    aliases: tuple[str, ...] = ()
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


class ResolverConfig(BaseModel):
    """The versioned cascade configuration (`resolver_versions` row shape)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver_version: _NonEmpty
    trigram_floor: Annotated[float, Field(ge=0.0, le=1.0)] = 0.3
    blocking_limit: Annotated[int, Field(ge=1)] = 10
    thresholds: ResolutionThresholds = ResolutionThresholds()


class T4Selection(BaseModel):
    """One binary T4 choice over a supplied candidate snapshot (D100)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["match", "new"]
    candidate_id: UUID | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    rationale: str | None = None

    @model_validator(mode="after")
    def candidate_matches_decision(self) -> Self:
        """Require an id for match and forbid one for new."""
        if self.decision == "match" and self.candidate_id is None:
            raise ValueError("match requires candidate_id")
        if self.decision == "new" and self.candidate_id is not None:
            raise ValueError("new forbids candidate_id")
        return self
