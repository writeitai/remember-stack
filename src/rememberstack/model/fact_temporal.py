"""Typed world-time state and evidence for D107/D110 fact application."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.queue import UTCDateTime


class FactTemporalKind(StrEnum):
    """Fact shape, independent of whether its source supplied a date."""

    STATE = "state"
    OCCURRENCE = "occurrence"
    UNKNOWN = "unknown"


class FactTemporalBasis(StrEnum):
    """Authority for one verdict endpoint; erased is distinct from unknown."""

    WORLD_TIME = "world_time"
    VERDICT = "verdict"
    SOURCE_REMOVED = "source_removed"
    LEGACY = "legacy"
    UNKNOWN = "unknown"
    ERASED = "erased"


class TemporalMembership(StrEnum):
    """Three-valued membership prevents erased dates from certifying absence."""

    INSIDE = "inside"
    OUTSIDE = "outside"
    UNCERTAIN = "uncertain"


class TemporalResult(StrEnum):
    """Application outcome; operational retries are separate from uncertainty."""

    APPLIED = "applied"
    NOOP = "noop"
    UNCERTAIN = "uncertain"
    REFUSED = "refused"
    STALE = "stale"


class ClaimTemporalWindow(BaseModel):
    """An immutable claim's raw inclusive D41 fields, without source-clock fallback.

    Callers load only non-forgotten claims. Withdrawn testimony still belongs in
    occurrence reduction; correction authority separately requires current support.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    kind: ClaimValidKind | None = None
    valid_from: UTCDateTime | None = None
    valid_until: UTCDateTime | None = None
    precision: ClaimValidPrecision = ClaimValidPrecision.UNKNOWN


class OccurrenceWindow(BaseModel):
    """Canonical half-open evidence union; precision describes input granularity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: UTCDateTime | None = None
    end: UTCDateTime | None = None
    precision: ClaimValidPrecision | None = None

    @model_validator(mode="after")
    def require_canonical_shape(self) -> Self:
        """Reject empty spans and inconsistent unknown/known metadata."""
        if self.precision is None:
            if self.start is not None or self.end is not None:
                raise ValueError("unknown occurrence has no endpoints")
        elif self.precision is ClaimValidPrecision.UNKNOWN or self.start is None:
            raise ValueError("known occurrence requires a start and known precision")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("occurrence interval must be non-empty")
        return self


class VerdictWindow(BaseModel):
    """One recorded fact window; NULL/erased does not mean unbounded authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: UTCDateTime | None = None
    end: UTCDateTime | None = None
    start_basis: FactTemporalBasis = FactTemporalBasis.UNKNOWN
    end_basis: FactTemporalBasis = FactTemporalBasis.UNKNOWN

    @model_validator(mode="after")
    def require_erased_null(self) -> Self:
        """Erasure must remove the actual timestamp, not merely relabel it."""
        if self.start_basis is FactTemporalBasis.ERASED and self.start is not None:
            raise ValueError("erased start must be NULL")
        if self.end_basis is FactTemporalBasis.ERASED and self.end is not None:
            raise ValueError("erased end must be NULL")
        return self


class FactTemporalState(BaseModel):
    """The temporal fields of a fact, independent of identity and statement text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FactTemporalKind
    verdict: VerdictWindow = Field(default_factory=VerdictWindow)
    occurrence: OccurrenceWindow = Field(default_factory=OccurrenceWindow)
    seed_claim_id: UUID | None = None
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None = None
    revision: int = Field(default=0, ge=0)
    from_operation_id: UUID | None = None
    until_operation_id: UUID | None = None
    contradiction_group: UUID | None = None

    @model_validator(mode="after")
    def require_fact_shape(self) -> Self:
        """Mirror the fact schema's nonempty-state and uncapped-occurrence rules."""
        if self.kind is FactTemporalKind.OCCURRENCE and self.verdict.end is not None:
            raise ValueError("occurrence verdict cannot be capped")
        if (
            self.kind is FactTemporalKind.STATE
            and self.verdict.start is not None
            and self.verdict.end is not None
            and self.verdict.end <= self.verdict.start
        ):
            raise ValueError("state verdict interval must be non-empty")
        return self


class ReversibleTemporalEffect(BaseModel):
    """A stored correction's endpoint effect, loaded from the operation journal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: UUID
    before: VerdictWindow
    after: VerdictWindow

    @property
    def changed_start(self) -> bool:
        """Changing an endpoint basis also transfers endpoint ownership."""
        return (self.before.start, self.before.start_basis) != (
            self.after.start,
            self.after.start_basis,
        )

    @property
    def changed_end(self) -> bool:
        """Report the recorded endpoint change, including its authority basis."""
        return (self.before.end, self.before.end_basis) != (
            self.after.end,
            self.after.end_basis,
        )
