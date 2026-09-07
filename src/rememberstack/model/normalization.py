"""Complete resolved normalization output and its immutable D110 publication receipt."""

from typing import Annotated
from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.relations import ClaimForNormalization

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Text = Annotated[str, Field(strict=True, min_length=1)]


class NormalizedRelation(BaseModel):
    """One resolved distinct triple, still unattached to any fact identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    subject_entity_id: UUID
    predicate: _Text
    object_entity_id: UUID
    shape_kind: FactTemporalKind = FactTemporalKind.UNKNOWN


class NormalizedObservation(BaseModel):
    """One accepted resolved observation retained before version-level materialization."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    subject_entity_id: UUID
    statement: _Text
    shape_kind: FactTemporalKind = FactTemporalKind.UNKNOWN


class NormalizationOutput(BaseModel):
    """Every accepted output from one claim, including an explicit empty disposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: Literal["accepted", "empty", "soft_drop"]
    relations: tuple[NormalizedRelation, ...] = ()
    observations: tuple[NormalizedObservation, ...] = ()

    @field_validator("relations")
    @classmethod
    def canonical_relations(
        cls, values: tuple[NormalizedRelation, ...]
    ) -> tuple[NormalizedRelation, ...]:
        """Deduplicate identical outputs without silently choosing conflicting shape metadata."""
        result: dict[tuple[UUID, str, UUID], NormalizedRelation] = {}
        for value in values:
            key = (value.subject_entity_id, value.predicate, value.object_entity_id)
            if key in result and result[key] != value:
                raise ValueError(
                    "one normalized triple has conflicting temporal shapes"
                )
            result[key] = value
        return tuple(result[key] for key in sorted(result))

    @field_validator("observations")
    @classmethod
    def canonical_observations(
        cls, values: tuple[NormalizedObservation, ...]
    ) -> tuple[NormalizedObservation, ...]:
        """Keep every distinct statement, with stable order and no conflicting duplicate shape."""
        result: dict[tuple[UUID, str], NormalizedObservation] = {}
        for value in values:
            key = (value.subject_entity_id, value.statement)
            if key in result and result[key] != value:
                raise ValueError(
                    "one normalized observation has conflicting temporal shapes"
                )
            result[key] = value
        return tuple(result[key] for key in sorted(result))

    @model_validator(mode="after")
    def require_complete_disposition(self) -> Self:
        """An accepted receipt has outputs; both empty dispositions have none."""
        if (self.outcome == "accepted") != bool(self.relations or self.observations):
            raise ValueError(
                "normalization disposition disagrees with its complete output"
            )
        return self


class NormalizationInput(BaseModel):
    """One source snapshot to prepare before inference and revalidate at publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: ClaimForNormalization
    temporal_window: ClaimTemporalWindow
    input_digest: _Digest


class NormalizationReceipt(BaseModel):
    """The first complete accepted answer for a claim and normalizer generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    receipt_id: UUID
    deployment_id: UUID
    claim_id: UUID
    doc_id: UUID
    normalizer_version: _Text
    input_digest: _Digest
    output_digest: _Digest
    output: NormalizationOutput
