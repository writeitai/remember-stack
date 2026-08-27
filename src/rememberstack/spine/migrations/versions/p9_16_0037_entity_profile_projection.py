"""Cut entity vectors from names to evidence-backed profiles (D95).

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
          embedding_input_policy_version = 'entity-profile-v1',
          ready = false,
          updated_at = now()
        WHERE target = 'entities' AND channel = 'semantic';
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
        """
    )
