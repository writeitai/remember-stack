"""D91 P1 Lance maintain units, table stats, and unlaned stage vocabulary.

revision: p9_11_0032
"""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_11_0032"
down_revision: str | None = "p9_10_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""ALTER TYPE pipeline_stage ADD VALUE IF NOT EXISTS 'maintain_p1_index';
ALTER TYPE processing_target ADD VALUE IF NOT EXISTS 'p1_maintain_unit';

CREATE TABLE p1_maintain_units (
  unit_id           uuid PRIMARY KEY,                 -- ledger processing_state.target_id
  deployment_id     uuid NOT NULL REFERENCES deployments, -- routing / attribution
  lance_root_key    text NOT NULL,                    -- canonical lance_root identity
  table_name        text NOT NULL,                    -- chunks | claims | facts | entities
  mode              text NOT NULL,                    -- light | heavy | ensure_indexes
  reason            text NOT NULL,                    -- last enqueue / coalesce reason
  force             boolean NOT NULL DEFAULT false,   -- monotonic admin/force override
  requested_at      timestamptz NOT NULL DEFAULT now(), -- bumped on pending/failed coalesce
  rerun_requested   boolean NOT NULL DEFAULT false,   -- set when enqueue races a live run
  last_heartbeat_at timestamptz,                      -- side-thread liveness (D91 §5.5.2)
  claimed_attempt   integer,                          -- last ClaimedWork.attempt stamp
  operator_state    text,                             -- denormalized awaiting_operator copy
  result            jsonb,                            -- optional terminal / defer payload
  created_at        timestamptz NOT NULL DEFAULT now(), -- insert time
  CHECK (table_name IN ('chunks', 'claims', 'facts', 'entities')),
  CHECK (mode IN ('light', 'heavy', 'ensure_indexes')),
  CHECK (operator_state IS NULL OR operator_state = 'awaiting_operator')
);
COMMENT ON TABLE p1_maintain_units IS
  'D91: one physical P1 maintain unit (lance_root, table, mode); ledger target_id is unit_id; open-ness is processing_state status, not a partial unique index.';
CREATE INDEX ix_p1_maintain_units_key
  ON p1_maintain_units (lance_root_key, table_name, mode);

CREATE TABLE p1_lance_table_stats (
  lance_root_key            text NOT NULL,            -- canonical lance_root identity
  table_name                text NOT NULL,            -- chunks | claims | facts | entities
  row_count                 bigint NOT NULL DEFAULT 0, -- last observed Lance count_rows
  last_light_at             timestamptz,              -- last successful light optimize
  last_heavy_at             timestamptz,              -- last successful IVF/FTS retrain
  last_heavy_row_count      bigint,                   -- train baseline; null until recorded
  last_unindexed_rows       bigint,                   -- last probed unindexed row count
  last_num_fragments        bigint,                   -- last probed fragment count
  last_num_small_fragments  bigint,                   -- last probed small-fragment count
  last_maintain_enqueue_at  timestamptz,              -- write-rate / defer hint
  last_error                text,                     -- last maintain error, if any
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
  'D91: table-scoped P1 Lance stats and heavy escalation; keyed by (lance_root_key, table_name) and survives successor units.';
"""


def upgrade() -> None:
    """Add maintain vocabulary, unit rows, and durable table stats."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop reversible D91 tables; additive enum values remain."""
    drop_tables(table_names=("p1_maintain_units", "p1_lance_table_stats"))
