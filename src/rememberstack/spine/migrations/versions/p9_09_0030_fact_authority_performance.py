"""Factor current fact evidence so public authorities expand visibility once.

revision: p9_09_0030

The public ``memory_v1`` contract is unchanged.  Two ungranted helpers own the
current claim association and D54 lineage aggregation; public fact views add
historical fact membership exactly once instead of recursively expanding the
same visibility tree through one another.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import _split_sql
from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
    MEMORY_V1_AUTHORED_DDL,
)
from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
    MEMORY_V1_CORRECTION_DDL,
)

revision: str = "p9_09_0030"
down_revision: str | None = "p9_08_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW_OWNER = "rememberstack_view_owner"
_QUERY_ROLE_PREFIX = "rememberstack_query"

FACT_AUTHORITY_HELPER_VIEWS: tuple[str, ...] = (
    "v_memory_fact_claim_live",
    "v_memory_evidence_lineage_live",
)

FACT_AUTHORITY_DDL = r"""
CREATE VIEW v_memory_fact_claim_live (
  deployment_id,
  fact_kind,
  fact_id,
  claim_id,
  stance,
  doc_id,
  source_kind,
  source_handle,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind,
  linked_at
) AS
SELECT
  evidence.deployment_id,
  'relation',
  evidence.relation_id,
  evidence.claim_id,
  evidence.stance::text,
  claim.doc_id,
  claim.source_kind,
  claim.source_handle,
  claim.asserted_at,
  claim.claim_valid_from,
  claim.claim_valid_until,
  claim.claim_valid_precision,
  claim.claim_valid_kind,
  evidence.created_at
FROM relation_evidence AS evidence
JOIN memory_v1.claims_live AS claim
  ON claim.deployment_id = evidence.deployment_id
 AND claim.claim_id = evidence.claim_id
 AND claim.doc_id = evidence.doc_id
UNION ALL
SELECT
  evidence.deployment_id,
  'observation',
  evidence.observation_id,
  evidence.claim_id,
  evidence.stance::text,
  claim.doc_id,
  claim.source_kind,
  claim.source_handle,
  claim.asserted_at,
  claim.claim_valid_from,
  claim.claim_valid_until,
  claim.claim_valid_precision,
  claim.claim_valid_kind,
  evidence.created_at
FROM observation_evidence AS evidence
JOIN memory_v1.claims_live AS claim
  ON claim.deployment_id = evidence.deployment_id
 AND claim.claim_id = evidence.claim_id
 AND claim.doc_id = evidence.doc_id;
COMMENT ON VIEW v_memory_fact_claim_live IS
  'Private single authority for a current-testimony claim-to-fact association. It binds every evidence row to the exact live claim lineage but deliberately does not assert that the referenced fact is visible; public fact relations add v_memory_fact_visible exactly once. Not part of memory_v1 and never granted to a query role.';

CREATE VIEW v_memory_evidence_lineage_live (
  deployment_id,
  fact_kind,
  fact_id,
  doc_id,
  stance,
  source_kind,
  source_handle,
  claim_count,
  representative_claim_id,
  asserted_from,
  asserted_to
) AS
SELECT
  evidence.deployment_id,
  evidence.fact_kind,
  evidence.fact_id,
  evidence.doc_id,
  evidence.stance,
  evidence.source_kind,
  evidence.source_handle,
  count(*)::bigint,
  (array_agg(
    evidence.claim_id
    ORDER BY evidence.asserted_at DESC NULLS LAST, evidence.claim_id
  ))[1],
  min(evidence.asserted_at),
  max(evidence.asserted_at)
FROM v_memory_fact_claim_live AS evidence
GROUP BY
  evidence.deployment_id,
  evidence.fact_kind,
  evidence.fact_id,
  evidence.doc_id,
  evidence.stance,
  evidence.source_kind,
  evidence.source_handle;
COMMENT ON VIEW v_memory_evidence_lineage_live IS
  'Private single authority for D54 current-testimony lineage aggregation, at one fact by document lineage by stance. It consumes v_memory_fact_claim_live and deliberately does not assert fact visibility; public fact relations add v_memory_fact_visible exactly once. Not part of memory_v1 and never granted to a query role.';

CREATE OR REPLACE VIEW memory_v1.fact_claim_evidence_live (
  deployment_id,
  fact_kind,
  fact_id,
  claim_id,
  stance,
  doc_id,
  source_kind,
  source_handle,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind,
  linked_at
) AS
SELECT evidence.*
FROM v_memory_fact_claim_live AS evidence
JOIN v_memory_fact_visible AS fact
  ON fact.deployment_id = evidence.deployment_id
 AND fact.fact_kind = evidence.fact_kind
 AND fact.fact_id = evidence.fact_id;

CREATE OR REPLACE VIEW memory_v1.evidence_lineage (
  deployment_id,
  fact_kind,
  fact_id,
  doc_id,
  stance,
  source_kind,
  source_handle,
  claim_count,
  representative_claim_id,
  asserted_from,
  asserted_to
) AS
SELECT lineage.*
FROM v_memory_evidence_lineage_live AS lineage
JOIN v_memory_fact_visible AS fact
  ON fact.deployment_id = lineage.deployment_id
 AND fact.fact_kind = lineage.fact_kind
 AND fact.fact_id = lineage.fact_id;

CREATE OR REPLACE VIEW memory_v1.facts_visible_history (
  deployment_id,
  fact_kind,
  fact_id,
  subject_entity_id,
  predicate,
  object_entity_id,
  statement,
  fact_label,
  valid_from,
  valid_until,
  ingested_at,
  invalidated_at,
  contradiction_group,
  confidence,
  evidence_count_current,
  contradict_count_current,
  support_state_current
) AS
SELECT
  fact.deployment_id,
  fact.fact_kind,
  fact.fact_id,
  fact.subject_entity_id,
  fact.predicate,
  fact.object_entity_id,
  fact.statement,
  fact.fact_label,
  fact.valid_from,
  fact.valid_until,
  fact.ingested_at,
  fact.invalidated_at,
  fact.contradiction_group,
  fact.confidence,
  counts.supports,
  counts.contradicts,
  CASE WHEN EXISTS (
    SELECT 1
    FROM review_queue AS queue
    WHERE queue.deployment_id = fact.deployment_id
      AND queue.item_kind = 'support_withdrawn'
      AND queue.status IN ('pending', 'deferred')
      AND queue.candidate ->> 'fact_kind' = fact.fact_kind
      AND queue.candidate ->> 'fact_id' = fact.fact_id::text
  ) THEN 'withdrawn' ELSE 'current' END
FROM v_memory_fact_visible AS fact
CROSS JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE lineage.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE lineage.stance = 'contradicts')::bigint AS contradicts
  FROM v_memory_evidence_lineage_live AS lineage
  WHERE lineage.deployment_id = fact.deployment_id
    AND lineage.fact_kind = fact.fact_kind
    AND lineage.fact_id = fact.fact_id
) AS counts;
"""

_RESTORED_VIEWS: tuple[str, ...] = (
    "memory_v1.fact_claim_evidence_live",
    "memory_v1.evidence_lineage",
    "memory_v1.facts_visible_history",
)


def _query_role_name(*, database: str) -> str:
    """Return the deployment login name already created by Batch B."""
    return f"{_QUERY_ROLE_PREFIX}_{database}"


def _prior_view_definitions() -> dict[str, str]:
    """Return the definitions this migration replaces, in dependency order."""
    definitions: dict[str, str] = {}
    for block in (*MEMORY_V1_AUTHORED_DDL, MEMORY_V1_CORRECTION_DDL):
        for statement in _split_sql(sql=block):
            normalized = " ".join(statement.split())
            for qualified_name in _RESTORED_VIEWS:
                if normalized.startswith(
                    f"CREATE VIEW {qualified_name} ("
                ) or normalized.startswith(
                    f"CREATE OR REPLACE VIEW {qualified_name} ("
                ):
                    definitions[qualified_name] = statement
    missing = set(_RESTORED_VIEWS) - set(definitions)
    if missing:
        raise RuntimeError(f"missing prior fact view definitions: {sorted(missing)}")
    return definitions


def upgrade() -> None:
    """Install shared fact authorities without expanding caller reachability."""
    apply_ddl(sql=FACT_AUTHORITY_DDL)
    database = str(
        op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()
    )
    query_role = _query_role_name(database=database)
    for helper in FACT_AUTHORITY_HELPER_VIEWS:
        op.execute(f"REVOKE ALL ON public.{helper} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON public.{helper} FROM {query_role}")
        op.execute(f"ALTER VIEW public.{helper} OWNER TO {_VIEW_OWNER}")


def downgrade() -> None:
    """Restore the previous public definitions and remove private helpers."""
    definitions = _prior_view_definitions()
    for qualified_name in _RESTORED_VIEWS:
        statement = definitions[qualified_name]
        if statement.lstrip().startswith("CREATE VIEW "):
            statement = statement.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1)
        op.execute(statement)
    op.execute("DROP VIEW v_memory_evidence_lineage_live")
    op.execute("DROP VIEW v_memory_fact_claim_live")
