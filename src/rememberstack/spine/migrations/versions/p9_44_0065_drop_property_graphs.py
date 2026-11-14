"""Retired SQL/PGQ cleanup revision, retained for migration ordering.

D157 uses PostgreSQL 18, which has no property graphs. Fresh migrations and
catalog repair create only relational views; no PG19 upgrade path is supported.
"""

from collections.abc import Sequence

revision: str = "p9_44_0065"
down_revision: str | None = "p9_43_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No objects to remove on the PostgreSQL 18 reference server."""


def downgrade() -> None:
    """No property graphs to restore on PostgreSQL 18."""
