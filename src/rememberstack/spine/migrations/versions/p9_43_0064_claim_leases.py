"""Claim leases: a running attempt holds its row only while its worker renews it.

revision: p9_43_0064

``processing_state.lease_until`` is set when an attempt is claimed and moved
forward by the worker while the handler runs. A running row whose lease has
passed belongs to a worker that was killed or stalled; the reaper returns it to
``pending`` (or dead-letters it once ``attempts`` reached ``max_attempts``).
Complete, fail and renewal are conditional on the attempt number, so a worker
whose lease was taken back cannot finish a newer attempt.

Rows already ``running`` when this migration runs get an expired lease, so the
first sweep recovers any claim a killed worker left behind. The value is only
meaningful while the row is ``running``.

The partial index serves the reaper's scan of running rows per stage.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_43_0064"
down_revision: str | None = "p9_42_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the lease column, backfill running rows as expired, index the sweep."""
    op.execute("ALTER TABLE processing_state ADD COLUMN lease_until timestamptz")
    op.execute(
        "UPDATE processing_state SET lease_until = now() WHERE status = 'running'"
    )
    op.execute(
        "ALTER TABLE processing_state ADD CONSTRAINT processing_state_lease_check "
        "CHECK (status <> 'running' OR lease_until IS NOT NULL)"
    )
    op.execute(
        "CREATE INDEX ix_procstate_running_lease "
        "ON processing_state (deployment_id, stage, lease_until) "
        "WHERE status = 'running'"
    )
    op.execute(
        "COMMENT ON COLUMN processing_state.lease_until IS "
        "'While status=running: the instant the claiming attempt''s lease ends "
        "unless its worker renews it. The reaper returns expired running rows to "
        "pending, or dead-letters them at max_attempts.'"
    )


def downgrade() -> None:
    """Drop the lease; running rows keep their status as before."""
    op.execute("DROP INDEX IF EXISTS ix_procstate_running_lease")
    op.execute(
        "ALTER TABLE processing_state DROP CONSTRAINT IF EXISTS "
        "processing_state_lease_check"
    )
    op.execute("ALTER TABLE processing_state DROP COLUMN lease_until")
