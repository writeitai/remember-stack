"""The single chosen world-time window on a mutable fact (D114)."""

from enum import StrEnum
from typing import Annotated
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.queue import UTCDateTime


class TemporalMatch(StrEnum):
    """How a chosen window matches this query, independently of fact confidence."""

    CONFIRMED = "confirmed"
    POSSIBLE = "possible"


class FactWindow(BaseModel):
    """Already canonical half-open endpoints; missing is distinct from open.

    Unlike claim windows, facts may have only one known endpoint. An entire
    value is replaced when adjudication revises dates; this immutable value
    object does not make a fact's chosen dates immutable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_from: UTCDateTime | None = None
    valid_until: UTCDateTime | None = None
    valid_precision: ClaimValidPrecision = ClaimValidPrecision.UNKNOWN

    @model_validator(mode="after")
    def coherent_window(self) -> Self:
        """Enforce D114 shapes without filling or recanonicalizing an endpoint."""
        start, end = self.valid_from, self.valid_until
        if self.valid_precision is ClaimValidPrecision.UNKNOWN:
            if start is not None or end is not None:
                raise ValueError("unknown precision requires both endpoints absent")
        elif self.valid_precision is ClaimValidPrecision.OPEN:
            if start is None or end is not None:
                raise ValueError("open requires a known start and no end")
        elif start is None and end is None:
            raise ValueError("a boundary precision requires a known endpoint")
        if start is not None and end is not None and end <= start:
            raise ValueError("fact windows must be nonempty half-open intervals")
        return self

    @property
    def is_complete(self) -> bool:
        """Whether containment can use both boundaries without inventing dates."""
        return self.valid_from is not None and (
            self.valid_until is not None
            or self.valid_precision is ClaimValidPrecision.OPEN
        )


class GroundedFactWindow(BaseModel):
    """One complete replacement plus evidence IDs validated against model inputs.

    An absent replacement preserves the fact's dates. A supplied all-unknown
    window explicitly clears them. The containing ordinary decision supplies
    rationale and confidence; no separate date-correction decision is needed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: FactWindow
    supporting_claim_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct_support(self) -> Self:
        """Reject repeated references rather than count them as independent support."""
        if len(set(self.supporting_claim_ids)) != len(self.supporting_claim_ids):
            raise ValueError("supporting claim IDs must be distinct")
        return self
