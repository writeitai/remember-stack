"""Typed values for RS-STATE-Learning-v1 runs."""

from __future__ import annotations

from typing import Annotated
from typing import Literal
from typing import Mapping

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

NonEmpty = Annotated[str, Field(min_length=1)]
Domain = Literal["travel", "customer_support", "shopping_assistant"]
Tier = Literal["smoke", "development", "publication"]
ArmKey = Literal[
    "empty", "full_context", "bm25", "dense", "mem0", "graphiti", "rememberstack"
]
SubProtocol = Literal["shared", "native"]


class FrozenModel(BaseModel):
    """Strict immutable base for durable benchmark boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TrajectoryDocument(FrozenModel):
    """One train trajectory rendered as an ingestible learning document."""

    domain: Domain
    task_id: NonEmpty
    source_ref: NonEmpty
    title: NonEmpty
    markdown: NonEmpty
    content_sha256: NonEmpty


class TaskManifest(FrozenModel):
    """Committed smoke/dev/publication task IDs per domain."""

    version: int
    tier: Tier
    protocol: NonEmpty
    state_bench_version: NonEmpty
    state_bench_commit: NonEmpty
    domains: Mapping[str, tuple[str, ...]]
    task_counts: Mapping[str, int]
    item_ids_sha256: NonEmpty


class RunConfiguration(FrozenModel):
    """Immutable prepare-time run fingerprint."""

    protocol: NonEmpty
    adapter_version: NonEmpty
    state_bench_commit: NonEmpty
    state_bench_version: NonEmpty
    state_bench_protocol_id: NonEmpty
    tier: Tier
    arm: ArmKey
    sub_protocol: SubProtocol
    domains: tuple[Domain, ...]
    repository_revision: NonEmpty
    recipe_name: NonEmpty
    render_format_version: NonEmpty
    top_k: int = Field(ge=1)
    num_runs: int = Field(ge=1)
    agent_model_name: NonEmpty
    agent_model_reasoning_level: str | None = None
    manifest_sha256: NonEmpty
    max_evaluator_cost_usd: float = Field(gt=0)
    state_bench_root: NonEmpty
    train_trajectories_root: NonEmpty


class MatrixCell(FrozenModel):
    """One independently schedulable (arm, sub_protocol, domain) evaluation cell."""

    arm: ArmKey
    sub_protocol: SubProtocol
    domain: Domain
    tier: Tier
    num_runs: int = Field(ge=1)
    num_workers: int = Field(ge=1)
    task_ids: tuple[str, ...]


class MatrixPlan(FrozenModel):
    """Full parallel evaluation matrix for one prepared campaign."""

    protocol: NonEmpty
    tier: Tier
    cells: tuple[MatrixCell, ...]
    cell_count: int = Field(ge=0)
