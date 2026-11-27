"""D163: point the graph view at the committed memory survivor walk."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_view_ddl
from rememberstack.spine.migrations.versions.p9_45_0066_fact_counts_demand import (
    SURVIVOR_DEMAND_DDL,
)

revision: str = "p9_57_0078"
down_revision: str | None = "p9_56_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRAPH_SURVIVOR_DDL = r"""
CREATE OR REPLACE VIEW v_graph_survivor (entity_id, survivor) AS
SELECT entity_id, survivor_entity_id FROM public.v_memory_entity_survivor;
"""


def upgrade() -> None:
    """Keep the graph view's two-column interface and its grants."""
    apply_view_ddl(sql=GRAPH_SURVIVOR_DDL)


def downgrade() -> None:
    """Restore an independent graph walk before restoring its adapter."""
    apply_view_ddl(sql=SURVIVOR_DEMAND_DDL)
