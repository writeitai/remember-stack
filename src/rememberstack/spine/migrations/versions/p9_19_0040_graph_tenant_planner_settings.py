"""Keep graph helper scans on deployment-first authority indexes.

revision: p9_19_0040
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_19_0040"
down_revision: str | None = "p9_18_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GRAPH_HELPER_INDEX_SETTINGS = r"""
ALTER FUNCTION memory_v1.graph_neighborhood(
  uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) SET enable_seqscan = off;
ALTER FUNCTION memory_v1.graph_path(
  uuid, uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) SET enable_seqscan = off;
"""

_GRAPH_HELPER_DEFAULT_SETTINGS = r"""
ALTER FUNCTION memory_v1.graph_neighborhood(
  uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) RESET enable_seqscan;
ALTER FUNCTION memory_v1.graph_path(
  uuid, uuid, uuid, integer, text[], timestamptz, timestamptz,
  integer, integer, integer, integer
) RESET enable_seqscan;
"""


def upgrade() -> None:
    """Pin recursive graph helpers to deployment-first indexed scans."""
    op.execute(_GRAPH_HELPER_INDEX_SETTINGS)


def downgrade() -> None:
    """Restore the preceding helpers' default planner settings."""
    op.execute(_GRAPH_HELPER_DEFAULT_SETTINGS)
