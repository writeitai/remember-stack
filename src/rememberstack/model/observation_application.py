"""Exact observation source and generation coordinates for D113 application."""

from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.queue import ProcessingLane


class ObservationVersionCoordinates(BaseModel):
    """A version's source contract and independently pinned semantic/flush generations."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: UUID
    version_id: UUID
    representation_id: UUID
    chunker_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    normalizer_version: str = Field(min_length=1)
    adjudicator_version: str = Field(min_length=1)
    flush_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    lane: ProcessingLane


class ObservationAdmissionHead(BaseModel):
    """One immutable ordinal of a closed canonical entity batch, before inference."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: UUID
    assertion_id: UUID
    ordinal: int = Field(gt=0)
    expected_inputs: int = Field(gt=0)
    adjudicator_version: str
    canonical_subject_entity_id: UUID
