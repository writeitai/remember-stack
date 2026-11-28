"""Statement-local eligibility for bounded native retrieval.

Only engine-owned SQL fragments enter these builders. Public source views own
source coordinates; the fact helper owns source/presentation truth. Membership
is shared inside a statement, never cached across requests or snapshots.
"""

from typing import Literal

from rememberstack.core.text_scope import fact_evidence_relation
from rememberstack.spine.migrations.versions.p9_55_0076_archive import (
    FACT_SOURCE_ARCHIVE_DDL,
)


def visible_entities_cte(
    *, entity_parameter: Literal["entity_ids", "filter_entity_ids"] = "entity_ids"
) -> str:
    """Qualify each distinct requested survivor once, without requesting counts."""
    return f"""
    scope_visible_entities AS MATERIALIZED (
        SELECT visible.entity_id
        FROM (SELECT DISTINCT unnest(CAST(:{entity_parameter} AS uuid[])) AS entity_id) requested
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :deployment_id
              AND entity.entity_id = requested.entity_id
            OFFSET 0
        ) visible
    )
    """


def candidate_mention_coverage_ctes(
    *,
    grain: Literal["claim", "chunk"],
    candidate_parameter: Literal["claim_ids", "chunk_ids"],
) -> str:
    """Constrain raw mentions by candidate IDs before current-content projection."""
    column = "claim_id" if grain == "claim" else "chunk_id"
    return (
        visible_entities_cte()
        + f""",
    scope_coverage AS MATERIALIZED (
        SELECT candidate.item_id,
               count(DISTINCT visible.entity_id)::integer AS coverage
        FROM (SELECT DISTINCT unnest(CAST(:{candidate_parameter} AS uuid[])) AS item_id) candidate
        CROSS JOIN LATERAL (
            SELECT mention.mention_id, mention.doc_id, mention.chunk_id, mention.created_at
            FROM mentions AS mention
            WHERE mention.deployment_id = :deployment_id
              AND mention.{column} = candidate.item_id
            OFFSET 0
        ) raw
        CROSS JOIN LATERAL (
            SELECT current.survivor_entity_id
            FROM public.v_memory_mention_current_content AS current
            WHERE current.deployment_id = :deployment_id
              AND current.mention_id = raw.mention_id
              AND current.created_at = raw.created_at
              AND current.doc_id = raw.doc_id
              AND current.chunk_id = raw.chunk_id
              AND current.{column} = candidate.item_id
            OFFSET 0
        ) current
        JOIN scope_visible_entities AS visible
          ON visible.entity_id = current.survivor_entity_id
        GROUP BY candidate.item_id
    )
    """
    )


def entity_mentions_ctes(
    *,
    grain: Literal["claim", "chunk"],
    entity_parameter: Literal["entity_ids", "filter_entity_ids"] = "entity_ids",
    content_scope: Literal["current", "readable"] = "current",
) -> str:
    """Enumerate current coverage or D140's explicit pending-lineage probe scope."""
    column = "claim_id" if grain == "claim" else "chunk_id"
    content = f"""
            SELECT current.mention_id, current.{column}, current.survivor_entity_id
            FROM public.v_memory_mention_current_content AS current
            WHERE current.deployment_id = :deployment_id
              AND current.mention_id = raw.mention_id
              AND current.created_at = raw.created_at
              AND current.doc_id = raw.doc_id
              AND current.chunk_id = raw.chunk_id
            OFFSET 0
    """
    if content_scope == "readable":
        # This does not confirm answer evidence. D140's counterfactual
        # freshness probe must discover lineages through older ready editions
        # even while their in-force replacement is still converting. Reuse
        # the version/readability authority, never hand-copy its tombstones.
        content = f"""
            SELECT raw.mention_id, raw.{column}, member.survivor_entity_id
            FROM memory_v1.chunks_all_versions_live AS chunk
            WHERE chunk.deployment_id = :deployment_id
              AND chunk.doc_id = raw.doc_id AND chunk.chunk_id = raw.chunk_id
            OFFSET 0
        """
    return (
        visible_entities_cte(entity_parameter=entity_parameter)
        + f""",
    scope_members(entity_id, survivor_entity_id) AS (
        SELECT entity_id, entity_id FROM scope_visible_entities
        UNION
        SELECT redirect.entity_id, member.survivor_entity_id
        FROM scope_members AS member
        JOIN entities AS redirect ON redirect.merged_into = member.entity_id
        WHERE redirect.deployment_id = :deployment_id
    ), scope_mentions AS MATERIALIZED (
        SELECT current.{column} AS item_id, member.survivor_entity_id,
               current.mention_id
        FROM scope_members AS member
        CROSS JOIN LATERAL (
            SELECT decision.mention_id, decision.decision_id
            FROM resolution_decisions AS decision
            WHERE decision.deployment_id = :deployment_id
              AND decision.entity_id = member.entity_id
              AND decision.superseded_by IS NULL
            OFFSET 0
        ) decision
        CROSS JOIN LATERAL (
            SELECT latest.decision_id FROM resolution_decisions AS latest
            WHERE latest.deployment_id = :deployment_id
              AND latest.mention_id = decision.mention_id
              AND latest.superseded_by IS NULL
            ORDER BY latest.decided_at DESC, latest.decision_id DESC LIMIT 1
        ) latest
        CROSS JOIN LATERAL (
            SELECT mention.mention_id, mention.doc_id, mention.chunk_id,
                   mention.claim_id, mention.created_at
            FROM mentions AS mention
            WHERE mention.deployment_id = :deployment_id
              AND mention.mention_id = decision.mention_id
              AND decision.decision_id = latest.decision_id
            OFFSET 0
        ) raw
        CROSS JOIN LATERAL ({content}) current
        WHERE current.{column} IS NOT NULL
          AND current.survivor_entity_id = member.survivor_entity_id
    ), scope_coverage AS MATERIALIZED (
        SELECT item_id, count(DISTINCT survivor_entity_id)::integer AS coverage
        FROM scope_mentions GROUP BY item_id
    )
    """
    )


def claim_entity_filter() -> str:
    """Check one fixed entity once and probe only each candidate claim's mentions."""
    return """
    EXISTS (SELECT 1 FROM memory_v1.entities_current entity
            WHERE entity.deployment_id=:deployment_id
              AND entity.entity_id=:filter_entity_id)
    AND EXISTS (
        SELECT 1 FROM LATERAL (
            SELECT mention.mention_id,mention.created_at,mention.doc_id,mention.chunk_id
            FROM mentions mention WHERE mention.deployment_id=:deployment_id
              AND mention.claim_id=indexed.claim_id OFFSET 0
        ) raw
        CROSS JOIN LATERAL (
            SELECT current.survivor_entity_id
            FROM public.v_memory_mention_current_content current
            WHERE current.deployment_id=:deployment_id
              AND current.mention_id=raw.mention_id AND current.created_at=raw.created_at
              AND current.doc_id=raw.doc_id AND current.chunk_id=raw.chunk_id
              AND current.claim_id=indexed.claim_id OFFSET 0
        ) current WHERE current.survivor_entity_id=:filter_entity_id
    )
    """


def _fact_has_visible_source(*, kind: Literal["relation", "observation"]) -> str:
    """Bind a fact's raw links before either branch of historical claim membership."""
    table, identifier = fact_evidence_relation(fact_kind=kind)
    # Keep deployment and document as filters on the chunk ID probe. With
    # sparse statistics the optimizer can otherwise favor a document index.
    return f"""
      EXISTS (
        SELECT 1
        FROM LATERAL (
          SELECT raw.deployment_id, raw.claim_id, raw.doc_id
          FROM {table} AS raw
          WHERE raw.deployment_id = stored.deployment_id AND raw.{identifier} = stored.{identifier}
          OFFSET 0
        ) AS evidence
        WHERE EXISTS (
          SELECT 1 FROM public.v_memory_claim_history_by_id AS history
          WHERE history.deployment_id = evidence.deployment_id
            AND history.claim_id = evidence.claim_id AND history.doc_id = evidence.doc_id
          OFFSET 0
        ) OR EXISTS (
          SELECT 1
          FROM LATERAL (
            SELECT c.deployment_id, c.claim_id, c.doc_id
            FROM public.claims AS c
            JOIN memory_v1.documents_live AS doc
              ON doc.deployment_id = c.deployment_id AND doc.doc_id = c.doc_id
            WHERE c.deployment_id = evidence.deployment_id
              AND c.claim_id = evidence.claim_id AND c.doc_id = evidence.doc_id
              AND c.is_current_testimony
            OFFSET 0
          ) AS claim
          WHERE EXISTS (
            SELECT 1 FROM public.document_version_scope AS mode_row
            WHERE mode_row.deployment_id = claim.deployment_id
              AND mode_row.doc_id = claim.doc_id AND mode_row.periodised
            OFFSET 0
          ) AND NOT EXISTS (
            SELECT 1 FROM public.v_memory_claim_history_by_id AS origin
            WHERE origin.deployment_id = claim.deployment_id
              AND origin.claim_id = claim.claim_id AND origin.doc_id = claim.doc_id
            OFFSET 0
          ) AND EXISTS (
            SELECT 1
            FROM LATERAL (
              SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims AS cc
              WHERE cc.deployment_id = claim.deployment_id AND cc.claim_id = claim.claim_id
              OFFSET 0
            ) AS occurrence
            CROSS JOIN LATERAL (
              SELECT ch.deployment_id, ch.version_id, ch.representation_id
              FROM public.chunks AS ch
              WHERE ch.chunk_id = occurrence.chunk_id
                AND ch.deployment_id IS NOT DISTINCT FROM occurrence.deployment_id
                AND ch.doc_id IS NOT DISTINCT FROM claim.doc_id
              OFFSET 0
            ) AS chunk
            CROSS JOIN LATERAL (
              SELECT 1 FROM public.document_versions AS version
              WHERE version.deployment_id = chunk.deployment_id
                AND version.version_id = chunk.version_id
                AND version.current_representation_id = chunk.representation_id
                AND version.deleted_at IS NULL
              OFFSET 0
            ) AS version
            CROSS JOIN LATERAL (
              SELECT 1 FROM public.document_representations AS reading
              WHERE reading.deployment_id = chunk.deployment_id
                AND reading.representation_id = chunk.representation_id
                AND reading.status = 'ready'
              OFFSET 0
            ) AS ready
          )
        )
      )
    """


def _fact_survivor_lookup(*, endpoint: Literal["subject", "object"]) -> str:
    return f"""
    CROSS JOIN LATERAL (
        SELECT survivor.survivor_entity_id FROM public.v_memory_entity_survivor AS survivor
        WHERE survivor.deployment_id = stored.deployment_id
          AND survivor.entity_id = stored.{endpoint}_entity_id
        OFFSET 0
    ) AS {endpoint}
    """


def _fact_source_arm(*, kind: Literal["relation", "observation"]) -> str:
    table = "relations" if kind == "relation" else "observations"
    identifier = f"{kind}_id"
    predicate = "stored.predicate" if kind == "relation" else "NULL::text"
    object_id = "object.survivor_entity_id" if kind == "relation" else "NULL::uuid"
    object_lookup = (
        _fact_survivor_lookup(endpoint="object") if kind == "relation" else ""
    )
    return f"""
    SELECT candidate.deployment_id, '{kind}'::text AS fact_kind, candidate.fact_id,
           candidate.subject_entity_id, candidate.predicate, candidate.object_entity_id,
           candidate.valid_from, candidate.valid_until, candidate.ingested_at,
           candidate.invalidated_at, candidate.contradiction_group, candidate.confidence,
           candidate.valid_precision
    FROM requested_facts AS requested
    CROSS JOIN LATERAL (
        SELECT stored.deployment_id, stored.{identifier} AS fact_id,
               subject.survivor_entity_id AS subject_entity_id, {predicate} AS predicate,
               {object_id} AS object_entity_id,
               stored.valid_from, stored.valid_until, stored.ingested_at,
               stored.invalidated_at, stored.contradiction_group, stored.confidence,
               stored.valid_precision::text AS valid_precision
        FROM public.{table} AS stored
        {_fact_survivor_lookup(endpoint="subject")}
        {object_lookup}
        WHERE requested.fact_kind = '{kind}' AND stored.deployment_id = :deployment_id
          AND stored.{identifier} = requested.fact_id
          AND {_fact_has_visible_source(kind=kind)}
        OFFSET 0
    ) AS candidate
    """


# Use the deployed D159 review expression and D54 count join verbatim. They
# refer only to the qualified fact key and can be projected after eligibility.
_FACT_SUPPORT_STATE = (
    FACT_SOURCE_ARCHIVE_DDL.split("counts.contradicts,\n", 1)[1]
    .split(",\n  fact.valid_precision\n", 1)[0]
    .strip()
)
_FACT_LINEAGE_COUNTS = (
    "LEFT JOIN LATERAL"
    + FACT_SOURCE_ARCHIVE_DDL.rsplit("\nLEFT JOIN LATERAL", 1)[1].split(";", 1)[0]
)


def _fact_presentation(*, presentation: Literal["keys", "support", "full"]) -> str:
    qualified = """
    FROM fact_sources AS fact JOIN fact_eligible_keys AS eligible
      ON eligible.fact_kind = fact.fact_kind AND eligible.fact_id = fact.fact_id
    """
    if presentation == "keys":
        return "SELECT fact.* " + qualified
    if presentation == "support":
        return (
            f"SELECT fact.*, {_FACT_SUPPORT_STATE} AS support_state_current {qualified}"
        )
    return f"""
    SELECT fact.deployment_id, fact.fact_kind, fact.fact_id,
           fact.subject_entity_id, fact.predicate, fact.object_entity_id,
           labels.statement, labels.fact_label, fact.valid_from, fact.valid_until,
           fact.ingested_at, fact.invalidated_at, fact.contradiction_group, fact.confidence,
           counts.supports AS evidence_count_current, counts.contradicts AS contradict_count_current,
           {_FACT_SUPPORT_STATE} AS support_state_current, fact.valid_precision
    {qualified}
    CROSS JOIN LATERAL (
        SELECT NULL::text AS statement, r.fact_label FROM public.relations AS r
        WHERE fact.fact_kind = 'relation' AND r.deployment_id = fact.deployment_id
          AND r.relation_id = fact.fact_id
        UNION ALL
        SELECT o.statement, o.obs_label FROM public.observations AS o
        WHERE fact.fact_kind = 'observation' AND o.deployment_id = fact.deployment_id
          AND o.observation_id = fact.fact_id
        OFFSET 0
    ) AS labels
    {_FACT_LINEAGE_COUNTS}
    """


def fact_eligibility_ctes(
    *, candidates_sql: str, presentation: Literal["keys", "support", "full"] = "full"
) -> str:
    """Qualify each fact key once by its source and resolved, visible endpoints.

    Match the public fact wrapper's source and endpoint rules with bounded
    probes. Only key, endpoint and filter fields materialize before hydration.
    """
    return f"""
    requested_facts AS MATERIALIZED (
        SELECT DISTINCT fact_kind, fact_id FROM ({candidates_sql}) AS input
    ), fact_sources AS MATERIALIZED (
        {_fact_source_arm(kind="relation")}
        UNION ALL
        {_fact_source_arm(kind="observation")}
    ), fact_endpoint_ids AS MATERIALIZED (
        SELECT subject_entity_id AS entity_id FROM fact_sources
        UNION SELECT object_entity_id FROM fact_sources WHERE object_entity_id IS NOT NULL
    ), fact_visible_endpoints AS MATERIALIZED (
        SELECT visible.entity_id FROM fact_endpoint_ids endpoint
        CROSS JOIN LATERAL (
            SELECT entity.entity_id FROM memory_v1.entities_current AS entity
            WHERE entity.deployment_id = :deployment_id
              AND entity.entity_id = endpoint.entity_id
            OFFSET 0
        ) visible
    ), fact_eligible_keys AS MATERIALIZED (
        SELECT source.fact_kind, source.fact_id FROM fact_sources source
        JOIN fact_visible_endpoints subject ON subject.entity_id = source.subject_entity_id
        WHERE source.fact_kind = 'observation' OR EXISTS (
            SELECT 1 FROM fact_visible_endpoints object
            WHERE object.entity_id = source.object_entity_id
        )
    ), eligible_facts AS NOT MATERIALIZED ({_fact_presentation(presentation=presentation)})
    """
