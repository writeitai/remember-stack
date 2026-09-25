"""D136 perimeter state: the last accepted signed revocation document."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_33_0054"
down_revision: str | None = "p9_32_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
CREATE TABLE perimeter_state (
  deployment_id uuid PRIMARY KEY REFERENCES deployments (deployment_id),
  seq bigint NOT NULL,         -- accepted document sequence; only a greater one replaces it
  document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),  -- verified claims of the accepted document
  accepted_at timestamptz NOT NULL DEFAULT now()  -- when this document was accepted
);
COMMENT ON TABLE perimeter_state IS
  'D136 last accepted signed revocation document per deployment, loaded at start-up so a restart cannot roll revocation back.';
"""


def upgrade() -> None:
    """Add the one-row-per-deployment perimeter state table."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop the perimeter state table."""
    drop_tables(table_names=("perimeter_state",))
