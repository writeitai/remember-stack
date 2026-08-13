"""D91 request-path surface cost ledger, meter state, and worker outcome.

revision: p9_11_0032
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "p9_11_0032"
down_revision: str | None = "p9_10_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add surface metering tables, worker outcome, and the union view."""
    op.execute(
        """
        CREATE TYPE surface_cost_kind AS ENUM (
          'search',
          'operation',
          'lookup',
          'open_query',
          'library'
        );
        CREATE TYPE surface_cost_outcome AS ENUM ('ok', 'provider_error');

        ALTER TABLE cost_ledger
          ADD COLUMN outcome surface_cost_outcome NOT NULL DEFAULT 'ok';

        CREATE INDEX ix_cost_export
          ON cost_ledger (deployment_id, occurred_at, cost_id);

        CREATE TABLE surface_cost_ledger (
          cost_id         uuid NOT NULL,
          deployment_id   uuid NOT NULL REFERENCES deployments,
          request_id      uuid NOT NULL,
          surface         surface_cost_kind NOT NULL,
          call_site       text NOT NULL,
          ordinal         integer NOT NULL,
          outcome         surface_cost_outcome NOT NULL,
          model_name      text NOT NULL,
          tokens_in       bigint NOT NULL,
          tokens_out      bigint NOT NULL,
          cost_usd        numeric(20,12) NOT NULL,
          latency_ms      integer NOT NULL,
          occurred_at     timestamptz NOT NULL,
          PRIMARY KEY (cost_id, occurred_at),
          CHECK (ordinal >= 1),
          CHECK (call_site ~ '^[a-z][a-z0-9_]*$')
        ) PARTITION BY RANGE (occurred_at);

        COMMENT ON TABLE surface_cost_ledger IS
          'Append-only provider-call attribution for request-path spend. No query text, vectors, or memory content. Distinct from cost_ledger (D67 worker attempts).';

        CREATE INDEX ix_surface_cost_export
          ON surface_cost_ledger (deployment_id, occurred_at, cost_id);

        CREATE TABLE surface_cost_meter_state (
          deployment_id        uuid PRIMARY KEY REFERENCES deployments,
          persist_failures     bigint NOT NULL DEFAULT 0 CHECK (persist_failures >= 0),
          scope_missing        bigint NOT NULL DEFAULT 0 CHECK (scope_missing >= 0),
          last_failure_at      timestamptz
        );

        COMMENT ON TABLE surface_cost_meter_state IS
          'Monotonic count of surface-meter persist failures and missing request scopes.';

        CREATE VIEW v_cost_receipts AS
        SELECT
          'worker'::text AS source,
          cost_id,
          deployment_id,
          processing_id AS work_id,
          stage::text AS stage,
          lane::text AS lane,
          attempt,
          NULL::text AS surface,
          call_key,
          outcome::text AS outcome,
          model_name,
          tokens_in,
          tokens_out,
          cost_usd,
          latency_ms,
          occurred_at
        FROM cost_ledger
        UNION ALL
        SELECT
          'surface'::text AS source,
          cost_id,
          deployment_id,
          request_id AS work_id,
          NULL::text AS stage,
          NULL::text AS lane,
          NULL::smallint AS attempt,
          surface::text AS surface,
          call_site || ':' || ordinal::text AS call_key,
          outcome::text AS outcome,
          model_name,
          tokens_in,
          tokens_out,
          cost_usd,
          latency_ms,
          occurred_at
        FROM surface_cost_ledger;
        """
    )
    op.execute(
        "SELECT public.create_parent("
        "p_parent_table := 'public.surface_cost_ledger', "
        "p_control := 'occurred_at', "
        "p_interval := '1 month', "
        "p_type := 'range', "
        "p_premake := 4, "
        "p_default_table := true, "
        "p_automatic_maintenance := 'on', "
        "p_jobmon := false)"
    )


def downgrade() -> None:
    """Drop D91 surface metering objects including the partman template."""
    op.execute("DROP VIEW IF EXISTS v_cost_receipts")
    op.execute(
        """
        DO $$
        DECLARE
          configured_template text;
        BEGIN
          SELECT template_table INTO configured_template
          FROM public.part_config
          WHERE parent_table = 'public.surface_cost_ledger';
          DELETE FROM public.part_config
          WHERE parent_table = 'public.surface_cost_ledger';
          IF configured_template IS NOT NULL THEN
            EXECUTE 'DROP TABLE IF EXISTS ' || configured_template || ' CASCADE';
          END IF;
        END
        $$;
        """
    )
    op.execute("DROP TABLE IF EXISTS surface_cost_ledger CASCADE")
    op.execute("DROP TABLE IF EXISTS surface_cost_meter_state")
    op.execute("DROP INDEX IF EXISTS ix_cost_export")
    op.execute("ALTER TABLE cost_ledger DROP COLUMN IF EXISTS outcome")
    op.execute("DROP TYPE IF EXISTS surface_cost_outcome")
    op.execute("DROP TYPE IF EXISTS surface_cost_kind")
