"""Park convert work with no configured route instead of dead-lettering it.

revision: p9_28_0049

D114 preserves otherwise admissible originals and parks conversion while the
configured route is absent. P3 separately exposes stored originals without
changing processed currency. This migration adds the durable parking reason;
projection and provider-mount wiring establish raw discoverability and access.

The CHECK is rewritten over `defer_reason::text` rather than the enum literal:
PostgreSQL refuses to use an enum value added in the same transaction that
adds it, and a text comparison sidesteps that without a second migration.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_28_0049"
down_revision: str | None = "p9_27_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Append the enum value and widen the status/defer_reason pairing rule."""
    op.execute("ALTER TYPE processing_defer_reason ADD VALUE IF NOT EXISTS 'no_route'")
    # The original CHECK is unnamed, so its generated name is positional and
    # cannot be relied on. Find it by the column it constrains instead.
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT con.conname INTO constraint_name
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = 'processing_state'
              AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) LIKE '%defer_reason%'
            LIMIT 1;
            IF constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE processing_state DROP CONSTRAINT %I',
                    constraint_name
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE processing_state ADD CONSTRAINT processing_state_defer_reason_check
        CHECK (
            (status = 'failed' AND defer_reason = 'retry_backoff') OR
            (status = 'pending' AND (
                defer_reason IS NULL
                OR defer_reason::text IN ('scheduled', 'budget', 'no_route')
            )) OR
            (status NOT IN ('pending', 'failed') AND defer_reason IS NULL)
        )
        """
    )
    # P3 checks durable raw availability for each selected version. Index the
    # unaccepted managed subset instead of scanning the measurement outbox per file.
    op.execute(
        "CREATE INDEX ix_managed_ingest_unaccepted_version "
        "ON managed_ingest_measurements (deployment_id, version_id) "
        "WHERE document_version_disposition = 'new_version' AND accepted_at IS NULL"
    )
    op.execute(
        "COMMENT ON TABLE processing_state IS "
        "'Per-(target,stage,version) idempotency and work-truth ledger (D12/D67). "
        "Route is deployment+stage+lane; not_before/defer_reason govern scheduling, "
        "retry backoff, no-attempt budget parking, and no_route parking for input "
        "this deployment has no converter for. The DLQ is status=dead_letter rows; "
        "delivery-provider metadata is never authoritative.'"
    )


def downgrade() -> None:
    """Restore the narrower pairing rule; the enum value stays (PostgreSQL
    cannot remove one in place). Any row still parked as `no_route` would
    violate the restored CHECK, so they are released to ordinary pending
    first. Older application code can then convert or fail according to its
    existing routing behavior; downgrade does not preserve D114 parking."""
    op.execute("DROP INDEX IF EXISTS ix_managed_ingest_unaccepted_version")
    op.execute(
        "UPDATE processing_state SET defer_reason = NULL "
        "WHERE defer_reason::text = 'no_route'"
    )
    op.execute(
        "ALTER TABLE processing_state "
        "DROP CONSTRAINT IF EXISTS processing_state_defer_reason_check"
    )
    op.execute(
        """
        ALTER TABLE processing_state ADD CONSTRAINT processing_state_defer_reason_check
        CHECK (
            (status = 'failed' AND defer_reason = 'retry_backoff') OR
            (status = 'pending' AND (
                defer_reason IS NULL
                OR defer_reason::text IN ('scheduled', 'budget')
            )) OR
            (status NOT IN ('pending', 'failed') AND defer_reason IS NULL)
        )
        """
    )
