"""`QueryResult/v1` — the provenance contract for every ad-hoc result (§4.4).

Every successful, empty, truncated, rejected, or failed query carries this
header before any rows. `exploratory_tabular` explicitly does not guarantee a
platform result grain, D49 negatives, contradiction completeness, exact
totals, or deterministic order; the design's non-guarantee list is normative.
The `snapshot_graph` grade and its `p2_snapshot` block belong to the Batch D
Cypher surface — the fields exist now so the contract shape is complete, and
stay null until then.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode

CONTRACT_VERSION = "QueryResult/v1"


class ResultColumn(BaseModel):
    """One projected column: name, SQL type, nullability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    nullable: bool


class ResultLimits(BaseModel):
    """The caps this request actually ran under."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_cap: int
    byte_cap: int
    statement_timeout_ms: int
    analytical_tier: bool


class P2Snapshot(BaseModel):
    """Cypher-only snapshot provenance (populated by Batch D, null before)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: UUID
    snapshot_version: str
    built_at: datetime
    age_seconds: float


class SemanticInvocation(BaseModel):
    """One §3.4 nomination invocation's disclosure (populated by Batch C)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    function: str
    nominated: int
    confirmed: int
    dropped_stale: int
    dropped_filtered: int = 0
    dropped_ambiguous: int = 0
    dropped_absent: int = 0
    dropped_body_mismatch: int = 0
    # The body path names which side was missing and which check failed, so a
    # reader can tell a deletion from a rebuild lag from a corrupted body.
    dropped_absent_current: int = 0
    dropped_absent_projection: int = 0
    dropped_hash_mismatch: int = 0
    # Both pins are reported separately: they are two different generations,
    # and collapsing them into one field cannot say which was applied.
    policy_generation: str | None = None
    embedder_generation: str | None = None
    generation: str | None = None
    pg_confirmed_at: datetime | None = None
    termination_reason: str | None = None


class QueryResult(BaseModel):
    """One complete `QueryResult/v1` response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["QueryResult/v1"] = CONTRACT_VERSION
    grade: Literal["exploratory_tabular", "snapshot_graph"] = "exploratory_tabular"
    request_id: UUID
    deployment_id: UUID
    surface_manifest_hash: str
    query_space_schema: Literal["memory_v1"] = "memory_v1"
    query_hash: str
    query_language: Literal["sql", "cypher"] = "sql"
    saved_query: dict[str, str] | None = None
    referenced_views: tuple[str, ...] = ()
    referenced_functions: tuple[str, ...] = ()
    source_grain_tags: tuple[str, ...] = ()
    columns: tuple[ResultColumn, ...] = ()
    rows: tuple[tuple[object, ...], ...] = ()
    returned_row_count: int = Field(ge=0, default=0)
    returned_byte_count: int = Field(ge=0, default=0)
    limits: ResultLimits
    truncated: bool = False
    truncation_reason: str | None = None
    exact_total_known: bool = False
    exact_total: int | None = None
    ordered_result: bool = False
    empty_result: bool = False
    negative_kind: None = None  # never a D49 negative on this surface (§4.1)
    execution_started_at: datetime
    evaluated_at: datetime | None = None
    pg_snapshot_at: datetime | None = None
    elapsed_ms: float = Field(ge=0)
    termination_reason: Literal["completed", "rejected", "failed"] = "completed"
    error_code: QueryErrorCode | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()
    semantic_invocations: tuple[SemanticInvocation, ...] = ()
    p2_snapshot: P2Snapshot | None = None
