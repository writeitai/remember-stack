"""Rename the three context operations and their composite contract (D114).

The catalog is deployment data rather than a seed owned by Alembic, so this
migration transforms existing canonical rows in place. Self-host bootstrap
will still reconcile them against the exact descriptors on startup.

revision: p9_28_0049
"""

from alembic import op

revision: str = "p9_28_0049"
down_revision: str | None = "p9_27_0048"
branch_labels = None
depends_on = None

_NEW_CHECK = r"""
ALTER TABLE assured_operations
ADD CONSTRAINT assured_operations_check CHECK (
  (name = 'resolve_entity' AND result_contract = 'envelope'
    AND output_grain = 'fact' AND answer_intent = 'identity') OR
  (name = 'claims_and_sources_context' AND result_contract = 'envelope'
    AND output_grain = 'evidence'
    AND answer_intent = 'claims_and_sources') OR
  (name = 'facts_context' AND result_contract = 'envelope'
    AND output_grain = 'fact' AND answer_intent = 'facts') OR
  (name = 'combined_context' AND result_contract = 'context_bundle_v2'
    AND output_grain IS NULL AND answer_intent = 'combined_context')
)
"""

_OLD_CHECK = r"""
ALTER TABLE assured_operations
ADD CONSTRAINT assured_operations_check CHECK (
  (name = 'resolve_entity' AND result_contract = 'envelope'
    AND output_grain = 'fact' AND answer_intent = 'identity') OR
  (name = 'testimony_context' AND result_contract = 'envelope'
    AND output_grain = 'evidence' AND answer_intent = 'testimony') OR
  (name = 'fact_context' AND result_contract = 'envelope'
    AND output_grain = 'fact' AND answer_intent = 'facts') OR
  (name = 'answer_context' AND result_contract = 'context_bundle_v1'
    AND output_grain IS NULL AND answer_intent = 'combined_context')
)
"""

_UPGRADE_ROWS = r"""
UPDATE assured_operations
SET description =
      'High-recall current claims and confirmed source passages.',
    execution_plan = replace(
      execution_plan::text,
      'testimony_context',
      'claims_and_sources_context'
    )::jsonb
WHERE name = 'claims_and_sources_context';

UPDATE assured_operations
SET execution_plan = replace(
      execution_plan::text,
      'fact_context',
      'facts_context'
    )::jsonb
WHERE name = 'facts_context';

UPDATE assured_operations
SET description =
      'Complete claims-and-sources and neighborhood-aware fact responses side by side in ContextBundle/v2.',
    result_schema = replace(
      replace(
        replace(
          replace(
            result_schema::text,
            'ContextBundle/v1',
            'ContextBundle/v2'
          ),
          'ContextBundleV1',
          'ContextBundleV2'
        ),
        'complete testimony and fact reads',
        'complete source and fact reads'
      ),
      '"testimony"',
      '"claims_and_sources"'
    )::jsonb,
    execution_plan = replace(
      replace(
        execution_plan::text,
        'testimony_context',
        'claims_and_sources_context'
      ),
      'fact_context',
      'facts_context'
    )::jsonb,
    version = 3
WHERE name = 'combined_context' AND version = 2;
"""

_DOWNGRADE_ROWS = r"""
UPDATE assured_operations
SET description =
      'High-recall current testimony: confirmed claims and source passages only.',
    execution_plan = replace(
      execution_plan::text,
      'claims_and_sources_context',
      'testimony_context'
    )::jsonb
WHERE name = 'claims_and_sources_context';

UPDATE assured_operations
SET execution_plan = replace(
      execution_plan::text,
      'facts_context',
      'fact_context'
    )::jsonb
WHERE name = 'facts_context';

UPDATE assured_operations
SET description =
      'Complete testimony and neighborhood-aware fact responses side by side in ContextBundle/v1.',
    result_schema = replace(
      replace(
        replace(
          replace(
            result_schema::text,
            'ContextBundle/v2',
            'ContextBundle/v1'
          ),
          'ContextBundleV2',
          'ContextBundleV1'
        ),
        'complete source and fact reads',
        'complete testimony and fact reads'
      ),
      '"claims_and_sources"',
      '"testimony"'
    )::jsonb,
    execution_plan = replace(
      replace(
        execution_plan::text,
        'claims_and_sources_context',
        'testimony_context'
      ),
      'facts_context',
      'fact_context'
    )::jsonb,
    version = 2
WHERE name = 'combined_context' AND version = 3;
"""


def upgrade() -> None:
    """Apply the D114 clean-cut operation and response names."""
    op.execute(
        "ALTER TABLE assured_operations DROP CONSTRAINT assured_operations_check"
    )
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'testimony_context' TO 'claims_and_sources_context'"
    )
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'fact_context' TO 'facts_context'"
    )
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'answer_context' TO 'combined_context'"
    )
    op.execute(
        "ALTER TYPE assured_result_contract RENAME VALUE "
        "'context_bundle_v1' TO 'context_bundle_v2'"
    )
    op.execute(
        "ALTER TYPE assured_answer_intent RENAME VALUE "
        "'testimony' TO 'claims_and_sources'"
    )
    op.execute(_UPGRADE_ROWS)
    op.execute(_NEW_CHECK)


def downgrade() -> None:
    """Restore the pre-D114 operation names and composite contract."""
    op.execute(
        "ALTER TABLE assured_operations DROP CONSTRAINT assured_operations_check"
    )
    op.execute(_DOWNGRADE_ROWS)
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'claims_and_sources_context' TO 'testimony_context'"
    )
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'facts_context' TO 'fact_context'"
    )
    op.execute(
        "ALTER TYPE assured_operation_name RENAME VALUE "
        "'combined_context' TO 'answer_context'"
    )
    op.execute(
        "ALTER TYPE assured_result_contract RENAME VALUE "
        "'context_bundle_v2' TO 'context_bundle_v1'"
    )
    op.execute(
        "ALTER TYPE assured_answer_intent RENAME VALUE "
        "'claims_and_sources' TO 'testimony'"
    )
    op.execute(_OLD_CHECK)
