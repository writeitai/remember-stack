"""Cut entity vectors from names to evidence-backed profiles (D95).

Deployment setup must keyset-backfill active entities through
``EntityProfileRefresher.backfill`` before republishing the semantic channel.
The migration also adds partial ranking indexes for bounded profile evidence
selection and deliberately leaves the channel unready until setup succeeds.

revision: p9_16_0037
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_16_0037"
down_revision: str | None = "p9_15_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Vacate unsafe name-only caches and require the profile input policy."""
    op.execute(
        """
        UPDATE entities SET
          profile_summary = NULL,
          embedding = NULL,
          embedding_model = NULL,
          embedding_input_policy_version = NULL,
          embedding_text_hash = NULL,
          updated_at = now()
        WHERE num_nonnulls(
          profile_summary, embedding, embedding_model,
          embedding_input_policy_version, embedding_text_hash
        ) > 0;

        UPDATE p1_search_channels SET
          embedding_input_policy_version = 'entity-profile-v2',
          ready = false,
          updated_at = now()
        WHERE target = 'entities' AND channel = 'semantic';

        CREATE INDEX ix_observations_profile_salience
          ON observations (
            deployment_id, subject_entity_id, evidence_count DESC,
            updated_at DESC, observation_id
          )
          WHERE invalidated_at IS NULL
            AND valid_until IS NULL
            AND evidence_count > 0;

        CREATE INDEX ix_relations_profile_subject_salience
          ON relations (
            deployment_id, subject_entity_id, evidence_count DESC,
            updated_at DESC, relation_id
          )
          WHERE invalidated_at IS NULL
            AND valid_until IS NULL
            AND evidence_count > 0;

        CREATE INDEX ix_relations_profile_object_salience
          ON relations (
            deployment_id, object_entity_id, evidence_count DESC,
            updated_at DESC, relation_id
          )
          WHERE invalidated_at IS NULL
            AND valid_until IS NULL
            AND evidence_count > 0;
        """
    )


def downgrade() -> None:
    """Vacate profile caches; lossy derived prose/vectors are not reconstructed."""
    op.execute(
        """
        UPDATE entities SET
          profile_summary = NULL,
          embedding = NULL,
          embedding_model = NULL,
          embedding_input_policy_version = NULL,
          embedding_text_hash = NULL,
          updated_at = now()
        WHERE num_nonnulls(
          profile_summary, embedding, embedding_model,
          embedding_input_policy_version, embedding_text_hash
        ) > 0;

        UPDATE p1_search_channels SET
          embedding_input_policy_version = 'entity-canonical-name-v1',
          ready = false,
          updated_at = now()
        WHERE target = 'entities' AND channel = 'semantic';

        DROP INDEX IF EXISTS ix_relations_profile_object_salience;
        DROP INDEX IF EXISTS ix_relations_profile_subject_salience;
        DROP INDEX IF EXISTS ix_observations_profile_salience;
        """
    )
