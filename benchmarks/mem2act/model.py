"""Typed models for Mem2Act runs."""

from __future__ import annotations

from typing import Annotated
from typing import Any
from typing import Literal
from typing import Mapping

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

NonEmpty = Annotated[str, Field(min_length=1)]
Tier = Literal["smoke", "development", "publication"]
ArmKey = Literal["empty", "rememberstack", "full_context"]


class FrozenModel(BaseModel):
    """Immutable boundary model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskManifest(FrozenModel):
    """Committed tier item list."""

    version: int
    tier: Tier
    protocol: NonEmpty
    dataset_commit: NonEmpty
    dataset_subdir: NonEmpty
    item_ids: tuple[str, ...]
    item_ids_sha256: NonEmpty
    item_count: int
    level_counts: Mapping[str, int]
    resolved_only: bool = True


class RunConfiguration(FrozenModel):
    """Prepare-time fingerprint."""

    protocol: NonEmpty
    adapter_version: NonEmpty
    dataset_commit: NonEmpty
    dataset_root: NonEmpty
    tier: Tier
    arm: ArmKey
    repository_revision: NonEmpty
    reader_model: NonEmpty
    recipe_name: NonEmpty
    top_k: int = Field(ge=1)
    manifest_sha256: NonEmpty
    max_evaluator_cost_usd: float = Field(gt=0)
    item_ids: tuple[str, ...]


class ScoreRecord(FrozenModel):
    """Per-item deterministic score."""

    qa_id: NonEmpty
    level: NonEmpty
    tool_name_ok: bool
    args_ok: bool
    item_ok: bool
    gold_name: NonEmpty
    predicted_name: str | None
    gold_arguments: Mapping[str, Any]
    predicted_arguments: Mapping[str, Any] | None
    failure: str | None = None
