"""D140 §7: citation paths traverse the current reference projection.

The graph's citation edge is one lineage-to-lineage edge per
``(from_doc_id, to_doc_id, kind)`` among the resolved references made by the
versions in force now (``rememberstack_graph_internal.crossrefs_live``). The
``memory_v1.graph_citation_path`` helper written by p9_17_0038 still expanded
``memory_v1.document_crossrefs_live`` row by row. Since D140 that view holds
the references of every non-tombstoned version, so a path could follow a
citation that only a superseded or repealed wording made, and two references
between the same documents gave two copies of one path (#520).

Two changes:

- ``crossrefs_live`` keeps its columns and its representative row (the
  smallest contributing ``crossref_id``, with that row's context and creation
  clock) and gains ``bindings`` and ``source_labels``: the distinct ``binding``
  values and the distinct non-null ``source_label`` values of every reference
  the edge merges, sorted (labels in byte order). Without them an edge would
  show only its smallest row's qualifiers, and a pinned or caller-flagged
  citation merged into it would read as a plain one. The property graph's
  edge properties are unchanged, so the view is replaced in place.
- ``graph_citation_path`` expands that projection instead of the public
  per-reference view. Its signature, result columns, budgets, comment and
  grants are unchanged; it is replaced in place.

The downgrade restores the p9_17 helper body and the p9_38 projection, which
drops the two columns; the property graph that depends on the view is dropped
and recreated around it with its grants.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.postgres_graph_sql import _replace_exact

revision: str = "p9_40_0061"
down_revision: str | None = "p9_39_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRAPH_CROSSREFS_PROJECTION_DDL = r"""
CREATE OR REPLACE VIEW rememberstack_graph_internal.crossrefs_live AS
SELECT x.deployment_id,
       (array_agg(x.crossref_id ORDER BY x.crossref_id))[1] AS crossref_id,
       x.from_doc_id, x.to_doc_id,
       x.kind::text AS kind,
       (array_agg(x.context ORDER BY x.crossref_id))[1] AS context,
       (array_agg(x.created_at ORDER BY x.crossref_id))[1] AS created_at,
       array_agg(DISTINCT x.binding::text ORDER BY x.binding::text) AS bindings,
       coalesce(
         array_agg(DISTINCT x.source_label COLLATE "C"
                   ORDER BY x.source_label COLLATE "C")
           FILTER (WHERE x.source_label IS NOT NULL),
         ARRAY[]::text[]
       ) AS source_labels
FROM document_crossrefs AS x
JOIN document_reference_generations AS generation
  ON generation.deployment_id = x.deployment_id
 AND generation.version_id = x.from_version_id
 AND generation.generation_id = x.generation_id
 AND generation.status = 'active'
JOIN document_version_scope AS scope
  ON scope.deployment_id = x.deployment_id
 AND scope.version_id = x.from_version_id
 AND scope.doc_id = x.from_doc_id
 AND scope.selectable
 AND scope.in_force @> statement_timestamp()
JOIN documents AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
 AND target.deleted_at IS NULL
WHERE x.resolved AND x.to_doc_id IS NOT NULL
GROUP BY x.deployment_id, x.from_doc_id, x.to_doc_id, x.kind;
"""
"""One lineage-to-lineage edge per (from_doc_id, to_doc_id, kind) among the
resolved references of the versions in force now, exactly as p9_38 projected
it, plus the qualifiers of every reference the edge merges. A filter on the
grouping columns (deployment and source lineage, as the helper probes) is
applied below the aggregation, so expansion still reads one lineage."""


def citation_path_helper() -> str:
    """The citation-path helper over the current reference projection."""
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as graph,
    )

    return _replace_exact(
        statement=_replaceable(sql=graph._CITATION_PATH_HELPER),
        old="FROM memory_v1.document_crossrefs_live AS x",
        new="FROM rememberstack_graph_internal.crossrefs_live AS x",
    )


def _replaceable(*, sql: str) -> str:
    """Replace the helper in place so its comment, owner and grants survive."""
    return _replace_exact(
        statement=sql,
        old="CREATE FUNCTION memory_v1.graph_citation_path(",
        new="CREATE OR REPLACE FUNCTION memory_v1.graph_citation_path(",
    )


def upgrade() -> None:
    """Project merged reference qualifiers and traverse that projection."""
    apply_ddl(sql=GRAPH_CROSSREFS_PROJECTION_DDL)
    op.execute(citation_path_helper())


def downgrade() -> None:
    """Restore the per-reference helper and the p9_38 projection."""
    from rememberstack.spine.migrations.versions import (
        p9_17_0038_postgres19_live_graph as graph,
    )
    from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
        GRAPH_CROSSREFS_SOURCE_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
        QUERY_ROLE_GRANTS,
    )

    op.execute(_replaceable(sql=graph._CITATION_PATH_HELPER))
    op.execute("DROP PROPERTY GRAPH memory_v1.memory_current")
    op.execute("DROP VIEW rememberstack_graph_internal.crossrefs_live")
    apply_ddl(sql=GRAPH_CROSSREFS_SOURCE_DDL)
    op.execute(graph._CURRENT_GRAPH)
    op.execute(QUERY_ROLE_GRANTS)
