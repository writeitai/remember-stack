"""E3 normalization values: LLM candidates, resolution, and fact records (D2-D5, D17-D18, D43)."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_NonEmpty = Annotated[str, Field(min_length=1)]


class EntityRef(BaseModel):
    """One entity as the normalizer emitted it: canonical name, optional surface.

    ``name`` is the nominative/canonical form. ``surface`` is the span as it
    appeared in the claim when it differs (``App`` vs ``Application``).
    Entity classes are rejected as extra input after the D96 hard cut.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _NonEmpty
    surface: str | None = None

    def mention_surface(self) -> str:
        """The claim spelling used for source aliases; falls back to ``name``."""
        if self.surface is None or not self.surface.strip():
            return self.name
        return self.surface.strip()


class RelationCandidate(BaseModel):
    """One (subject, predicate, object) proposal from the normalizer call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: EntityRef
    predicate: _NonEmpty
    object: EntityRef


class ObservationCandidate(BaseModel):
    """One entity-anchored value/statement proposal (D43), incl. stances (D59)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: EntityRef
    statement: _NonEmpty


class ObservationAssertion(BaseModel):
    """One resolved observation input in a document/entity adjudication batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: _NonEmpty
    claim_id: UUID
    doc_id: UUID


class NormalizationResponse(BaseModel):
    """The normalizer call's structured output for one claim (0..n of each)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relations: tuple[RelationCandidate, ...] = ()
    observations: tuple[ObservationCandidate, ...] = ()


class ClaimForNormalization(BaseModel):
    """One accepted claim as the normalize stage loads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: UUID
    deployment_id: UUID
    doc_id: UUID
    chunk_id: UUID
    claim_text: str
    is_attributed: bool
    extractor_version: str


class ResolvedEntity(BaseModel):
    """A resolution outcome: the canonical id and whether it was minted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: UUID
    created: bool
