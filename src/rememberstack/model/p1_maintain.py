"""Reports from explicit P1 Lance index maintenance (D91)."""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


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
    indexes_healthy: bool = True


class MaintainReport(BaseModel):
    """Per-table outcomes of one ensure / optimize / rebuild call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tables: tuple[TableMaintainStats, ...] = Field(default_factory=tuple)
