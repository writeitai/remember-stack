"""Reports from explicit P1 Lance index maintenance (D91)."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.queue import UTCDateTime


class TableMaintainStats(BaseModel):
    """One table's maintenance snapshot or operation outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    row_count_before: int = 0
    row_count: int = 0
    unindexed_rows_before: int = 0
    unindexed_rows: int = 0
    num_fragments_before: int = 0
    num_fragments: int = 0
    num_small_fragments_before: int = 0
    num_small_fragments: int = 0
    duration_ms: int = 0
    conflicts_retried: int = 0
    skipped: str | None = None
    operation: str | None = None


class MaintainReport(BaseModel):
    """Per-table outcomes of one ensure / optimize / rebuild call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tables: tuple[TableMaintainStats, ...] = Field(default_factory=tuple)


class P1MaintainMode(StrEnum):
    """One physical maintain unit's Lance operation family."""

    LIGHT = "light"
    HEAVY = "heavy"
    ENSURE_INDEXES = "ensure_indexes"


class P1MaintainTable(StrEnum):
    """The four P1 Lance tables continuous maintain may own."""

    CHUNKS = "chunks"
    CLAIMS = "claims"
    FACTS = "facts"
    ENTITIES = "entities"


class P1MaintainEnqueueRequest(BaseModel):
    """One request to open or coalesce a table-scoped maintain unit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    lance_root_key: str = Field(min_length=1)
    table_name: P1MaintainTable
    mode: P1MaintainMode
    reason: str = Field(min_length=1)
    force: bool = False
    not_before: UTCDateTime | None = None


class P1MaintainEnqueueResult(BaseModel):
    """What enqueue did: created, coalesced, marked rerun, or skipped by gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: UUID | None = None
    processing_id: UUID | None = None
    created: bool = False
    coalesced: bool = False
    rerun_requested: bool = False
    skipped: str | None = None


class P1MaintainCompleteRequest(BaseModel):
    """Attempt-fenced completion of one claimed maintain unit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    processing_id: UUID
    unit_id: UUID
    expected_attempt: int = Field(ge=1)
    deferred_successor_not_before: UTCDateTime | None = None
    skip_successor: bool = False
    result: dict[str, object] | None = None
    successor_reason: str | None = None
