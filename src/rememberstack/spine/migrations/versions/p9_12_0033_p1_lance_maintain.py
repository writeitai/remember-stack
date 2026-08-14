"""D93 durable P1 Lance table stats for the maintain ticker.

revision: p9_12_0033
"""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_12_0033"
down_revision: str | None = "p9_11_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""CREATE TABLE p1_lance_table_stats (
  lance_root_key            text NOT NULL,            -- canonical lance_root identity
  table_name                text NOT NULL,            -- chunks | claims | facts | entities
  row_count                 bigint NOT NULL DEFAULT 0, -- last observed Lance count_rows
  last_light_at             timestamptz,              -- last successful compact
  last_heavy_at             timestamptz,              -- last successful IVF/FTS retrain
  last_heavy_row_count      bigint,                   -- train baseline; null until recorded
  last_unindexed_rows       bigint,                   -- last probed unindexed row count
  last_num_fragments        bigint,                   -- last probed fragment count
  last_num_small_fragments  bigint,                   -- last probed small-fragment count
  last_maintain_enqueue_at  timestamptz,              -- last writer stats bump
  last_error                text,                     -- last ticker error, if any
  last_operation            text,                     -- ensure | compact | retrain
  changed_rows_since_heavy  bigint NOT NULL DEFAULT 0, -- vector rewrites since last heavy
  change_mass_since_heavy   double precision NOT NULL DEFAULT 0, -- char-capped rewrite mass
  rate_defer_count          integer NOT NULL DEFAULT 0, -- consecutive pure rate-defers
  conflict_defer_count      integer NOT NULL DEFAULT 0, -- consecutive post-train conflicts
  first_defer_at            timestamptz,              -- start of current defer streak
  operator_state            text,                     -- null | awaiting_operator
  writer_gate               text NOT NULL DEFAULT 'run', -- run | hold quiet-window gate
  updated_at                timestamptz NOT NULL DEFAULT now(), -- last stats write
  PRIMARY KEY (lance_root_key, table_name),
  CHECK (table_name IN ('chunks', 'claims', 'facts', 'entities')),
  CHECK (writer_gate IN ('run', 'hold')),
  CHECK (operator_state IS NULL OR operator_state = 'awaiting_operator')
);
COMMENT ON TABLE p1_lance_table_stats IS
  'D93: table-scoped P1 Lance stats for the maintain ticker; keyed by (lance_root_key, table_name).';
"""


def upgrade() -> None:
    """Add durable per-table Lance maintenance stats."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop the D93 stats table."""
    drop_tables(table_names=("p1_lance_table_stats",))
