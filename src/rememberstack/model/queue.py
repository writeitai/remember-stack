"""D67 queue-route snapshots and the binding Postgres stage/lane vocabulary."""

from datetime import datetime
from datetime import timedelta
from enum import StrEnum
from typing import Annotated
from typing import TypeAlias
from uuid import UUID

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PipelineStage(StrEnum):
    """Exact values of the binding Postgres ``pipeline_stage`` enum."""

    INGEST = "ingest"
    CONVERT = "convert"
    STRUCTURE = "structure"
    CROSSREF = "crossref"
    CHUNK = "chunk"
    EMBED_CHUNK = "embed_chunk"
    EXTRACT_CLAIMS = "extract_claims"
    EMBED_CLAIM = "embed_claim"
    GROUND_CLAIMS = "ground_claims"
    RESOLVE_ENTITIES = "resolve_entities"
    NORMALIZE_RELATIONS = "normalize_relations"
    ADJUDICATE_SUPERSESSION = "adjudicate_supersession"
    ADJUDICATE_OBSERVATIONS = "adjudicate_observations"
    EMBED_RELATION = "embed_relation"
    LABEL_RELATION = "label_relation"
    EMBED_OBSERVATION = "embed_observation"
    LABEL_OBSERVATION = "label_observation"
    REFRESH_PROFILE = "refresh_profile"
    BUILD_SNAPSHOT = "build_snapshot"
    DETECT_COMMUNITIES = "detect_communities"
    COMPILE_KNOWLEDGE = "compile_knowledge"
    REFLECT_KNOWLEDGE = "reflect_knowledge"
    LINT_KNOWLEDGE = "lint_knowledge"
    RECONCILE = "reconcile"
    DISPATCH_KNOWLEDGE = "dispatch_knowledge"  # appended by WP-6.6
    HARD_FORGET = "hard_forget"
    MAINTAIN_P1_INDEX = "maintain_p1_index"


class ProcessingLane(StrEnum):
    """The two and only two lane values used by Plane-E work."""

    STEADY = "steady"
    BACKFILL = "backfill"


def _require_utc(value: datetime) -> datetime:
    """Require an aware datetime whose UTC offset is exactly zero."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value


UTCDateTime: TypeAlias = Annotated[
    datetime, Field(strict=True), AfterValidator(_require_utc)
]


class QueueRoute(BaseModel):
    """Non-authoritative D67 physical-delivery route snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    stage: PipelineStage
    lane: ProcessingLane | None
