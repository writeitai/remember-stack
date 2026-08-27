"""Clustering values (D21): config, merge outcomes, and neighborhood reports.

The asymmetry every number serves: over-merging is catastrophic and silent;
under-merging is gradual and recoverable — so the machinery is paranoid in
one direction, and every merge is reversible.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ClusterConfig(BaseModel):
    """The decide-stage parameters (starting points to measure, D22).

    `distance_cut` is the HAC cut on cosine DISTANCE (1 - similarity):
    pieces below the cut are one entity. `blob_cap` is the black-hole
    guard; `blast_radius_cap` routes big merges to human review (D24).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_merge_enabled: bool = False
    """Fail closed until a profile-space clustering cut has evaluation provenance."""
    distance_cut: Annotated[float, Field(ge=0.0, le=2.0)] = 0.15
    blob_cap: Annotated[int, Field(ge=2)] = 50
    blast_radius_cap: Annotated[int, Field(ge=1)] = 100


class MergeProposal(BaseModel):
    """One decide-stage grouping: entities the cut placed in one piece."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    survivor_id: UUID
    absorbed_ids: tuple[UUID, ...]
    blast_radius: int = Field(ge=0)
    mean_distance: float


class NeighborhoodReport(BaseModel):
    """What one neighborhood re-decision did (nDR, registries §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    members: int
    merged: tuple[UUID, ...] = ()  # merge_event ids written
    queued_for_review: int = 0
    black_hole_tightened: bool = False


class UnmergeError(Exception):
    """The merge event cannot be reversed (already reversed, or unknown)."""


class MergeApplicationError(Exception):
    """A proposed merge is stale or cannot resolve to a safe live redirect."""
