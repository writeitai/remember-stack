"""D163: add a private historical origin probe for native fact reads."""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_58_0079"
down_revision: str | None = "p9_57_0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The non-null deployment and document checks are filters on the chunk ID
# probe. PostgreSQL can otherwise favor a document index with sparse stats.
CLAIM_HISTORY_BY_ID_DDL = r"""
CREATE VIEW v_memory_claim_history_by_id (
  deployment_id,
  claim_id,
  doc_id
) AS
SELECT claim.deployment_id, claim.claim_id, claim.doc_id
FROM public.claims AS claim
JOIN memory_v1.documents_live AS doc
  ON doc.deployment_id = claim.deployment_id AND doc.doc_id = claim.doc_id
WHERE EXISTS (
  SELECT 1
  FROM LATERAL (
    SELECT ch.version_id, ch.representation_id
    FROM public.chunks AS ch
    WHERE ch.chunk_id = claim.chunk_id
      AND ch.deployment_id IS NOT DISTINCT FROM claim.deployment_id
      AND ch.doc_id IS NOT DISTINCT FROM claim.doc_id
    OFFSET 0
  ) AS origin
  CROSS JOIN LATERAL (
    SELECT 1 FROM memory_v1.document_versions_visible AS version
    WHERE version.deployment_id = claim.deployment_id
      AND version.doc_id = claim.doc_id
      AND version.version_id = origin.version_id
    OFFSET 0
  ) AS visible
  CROSS JOIN LATERAL (
    SELECT 1 FROM public.document_representations AS reading
    WHERE reading.deployment_id = claim.deployment_id
      AND reading.version_id = origin.version_id
      AND reading.representation_id = origin.representation_id
      AND reading.status = 'ready'
    OFFSET 0
  ) AS ready
);
COMMENT ON VIEW v_memory_claim_history_by_id IS
  'Private native fact source lookup. Binds a claim origin to its live document, visible version and ready reading, including a superseded version or replaced reading. Broad SQL uses claims_visible_history.';
"""


def upgrade() -> None:
    apply_view_ddl(sql=CLAIM_HISTORY_BY_ID_DDL)
    connection = op.get_bind()
    database = connection.exec_driver_sql("SELECT current_database()").scalar_one()
    query_role = connection.dialect.identifier_preparer.quote(
        f"rememberstack_query_{database}"
    )
    op.execute("REVOKE ALL ON public.v_memory_claim_history_by_id FROM PUBLIC")
    op.execute(f"REVOKE ALL ON public.v_memory_claim_history_by_id FROM {query_role}")
    op.execute(
        "ALTER VIEW public.v_memory_claim_history_by_id OWNER TO rememberstack_view_owner"
    )


def downgrade() -> None:
    op.execute("DROP VIEW public.v_memory_claim_history_by_id")
