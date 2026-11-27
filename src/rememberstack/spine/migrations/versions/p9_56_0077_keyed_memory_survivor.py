"""D163: install the deployment-keyed walk before reversing the dependency."""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_view_ddl
from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
    MEMORY_V1_CORRECTION_DDL,
)

revision: str = "p9_56_0077"
down_revision: str | None = "p9_55_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEMORY_SURVIVOR_DDL = r"""
CREATE OR REPLACE VIEW v_memory_entity_survivor (
  deployment_id,
  entity_id,
  survivor_entity_id
) AS
SELECT seed.deployment_id, seed.entity_id, terminal.entity_id
FROM public.entities AS seed
CROSS JOIN LATERAL (
  WITH RECURSIVE chain(entity_id, merged_into) AS (
    SELECT seed.entity_id, seed.merged_into
    UNION
    SELECT target.entity_id, target.merged_into
    FROM chain
    CROSS JOIN LATERAL (
      SELECT next.entity_id, next.merged_into
      FROM public.entities AS next
      WHERE next.deployment_id = seed.deployment_id
        AND next.entity_id = chain.merged_into
      OFFSET 0
    ) AS target
  )
  SELECT entity_id FROM chain WHERE merged_into IS NULL
) AS terminal;
COMMENT ON VIEW v_memory_entity_survivor IS
  'Private deployment-scoped merge walk. The seed and every redirect belong to the same deployment. Cyclic, dangling or foreign redirects yield no terminal row. A bounded internal graph role may receive exact-column read access.';
"""


def upgrade() -> None:
    """Replace just this view, preserving its owner and column grants."""
    apply_view_ddl(sql=MEMORY_SURVIVOR_DDL)


def downgrade() -> None:
    """The graph view is already independent when the adapter is restored."""
    for statement in _split_sql(sql=MEMORY_V1_CORRECTION_DDL):
        if statement.startswith(
            (
                "CREATE OR REPLACE VIEW v_memory_entity_survivor (",
                "COMMENT ON VIEW v_memory_entity_survivor IS",
            )
        ):
            op.execute(statement)
