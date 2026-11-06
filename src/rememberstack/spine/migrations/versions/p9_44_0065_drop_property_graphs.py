"""Drop the SQL/PGQ property graphs (D98, PostgreSQL 19 RC 1 amendment).

PostgreSQL 19 Beta 4 reverted SQL/PGQ, so RC 1 and the final release have no
``CREATE PROPERTY GRAPH`` or ``GRAPH_TABLE``. The live graph stays: its nodes
and edges are the private ``rememberstack_graph_internal`` views, and the
one-hop read is now a plain join over them. Only the two property graphs
(``memory_v1.memory_current`` and ``memory_v1.memory_history``), which were
catalog metadata over those views, go away.

A Beta 3 store at the prior head still holds them, and they would block its
move to RC 1: ``pg_upgrade`` and ``pg_restore`` replay their DDL, which RC 1
cannot parse. This revision drops them where they exist, so the upgrade order
is: run this migration on Beta 3, then move the data to RC 1. On RC 1 there is
nothing to drop and the revision is a no-op.

The downgrade is a no-op. It does not recreate them, because the reference
server (RC 1 and later) cannot parse their DDL; nothing in this repository
reads them any more.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import DROP_PROPERTY_GRAPHS

revision: str = "p9_44_0065"
down_revision: str | None = "p9_43_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the two property graphs where a Beta 3 store still has them."""
    op.execute(DROP_PROPERTY_GRAPHS)


def downgrade() -> None:
    """Nothing to restore: PostgreSQL 19 RC 1 and later have no property graphs."""
