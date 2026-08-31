"""Park convert work with no configured route instead of dead-lettering it.

revision: p9_28_0049

An upload whose MIME the deployment has no conversion route for used to be
admitted, stored, and then discovered as unroutable inside the convert worker,
which marked the version failed and dead-lettered the work row. The document
still reached the corpus filesystem as a stub carrying its `raw_uri` -- an
agent could mount and read the original -- so the bytes were never the problem.
The problem was the *work*: a row in the dead-letter queue for something that
was never broken, only unsupported, and a version marked `failed` for the same
reason.

`no_route` makes that state say what it is. The convert row is enqueued
already parked: pending, no attempt consumed, no error recorded, and therefore
outside the DLQ. Registering the converter and resuming the parked rows
converts the backlog, which is exactly the recovery the dead-letter path could
not offer -- adding a route never rescued a version that had already failed
without one.

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
    first -- the convert stage then re-parks or converts them on its own
    terms rather than the downgrade silently stranding them."""
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
