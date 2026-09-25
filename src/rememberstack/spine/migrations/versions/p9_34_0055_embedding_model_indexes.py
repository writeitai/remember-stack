"""Index the model stamp on stored vectors so setup's model check is a lookup.

revision: p9_34_0055
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p9_34_0055"
down_revision: str | None = "p9_33_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("chunk_search", "claims", "relations", "observations")

_DDL = "\n".join(
    f"CREATE INDEX ix_{table}_embedding_model"
    f" ON {table} (deployment_id, embedding_model);"
    for table in _TABLES
)


def upgrade() -> None:
    """Add one (deployment_id, embedding_model) btree per vector table."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop the model-stamp indexes."""
    for table in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_embedding_model")
