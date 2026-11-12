"""Statement-local D153 eligibility for bounded native retrieval.

Only engine-owned SQL fragments enter these builders. Public source views own
source coordinates; the fact helper owns source/presentation truth. Membership
is shared inside a statement, never cached across requests or snapshots.
"""

from typing import Literal


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
            SELECT mention.mention_id, mention.doc_id, mention.chunk_id
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
) -> str:
    """Enumerate canonical current mentions from requested identities, not a corpus scan."""
    column = "claim_id" if grain == "claim" else "chunk_id"
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
            SELECT mention.mention_id, mention.doc_id, mention.chunk_id
            FROM mentions AS mention
            WHERE mention.deployment_id = :deployment_id
              AND mention.mention_id = decision.mention_id
              AND decision.decision_id = latest.decision_id
            OFFSET 0
        ) raw
        CROSS JOIN LATERAL (
            SELECT current.mention_id, current.{column}, current.survivor_entity_id
            FROM public.v_memory_mention_current_content AS current
            WHERE current.deployment_id = :deployment_id
              AND current.mention_id = raw.mention_id
              AND current.doc_id = raw.doc_id
              AND current.chunk_id = raw.chunk_id
            OFFSET 0
        ) current
        WHERE current.{column} IS NOT NULL
          AND current.survivor_entity_id = member.survivor_entity_id
    ), scope_coverage AS MATERIALIZED (
        SELECT item_id, count(DISTINCT survivor_entity_id)::integer AS coverage
        FROM scope_mentions GROUP BY item_id
    )
    """
    )


def fact_eligibility_ctes(*, candidates_sql: str) -> str:
    """Share endpoint qualification for internal candidate (fact_kind, fact_id) SQL.

    The endpoint conjunction is identical to the immutable D153 fact wrapper.
    Source/history/count logic remains exclusively in the private helper. Only
    identity/endpoint columns materialize before eligibility; presentation is
    hydrated through per-ID lookups afterwards, with unrequested counts prunable.
    """
    return f"""
    fact_sources AS MATERIALIZED (
        SELECT source.deployment_id, source.fact_kind, source.fact_id,
               source.subject_entity_id, source.object_entity_id
        FROM ({candidates_sql}) requested
        CROSS JOIN LATERAL (
            SELECT fact.deployment_id, fact.fact_kind, fact.fact_id,
                   fact.subject_entity_id, fact.object_entity_id
            FROM public.v_memory_fact_source_qualified AS fact
            WHERE fact.deployment_id = :deployment_id
              AND fact.fact_kind = requested.fact_kind
              AND fact.fact_id = requested.fact_id
            OFFSET 0
        ) source
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
    ), eligible_facts AS NOT MATERIALIZED (
        SELECT presentation.* FROM fact_eligible_keys eligible
        CROSS JOIN LATERAL (
            SELECT fact.* FROM public.v_memory_fact_source_qualified fact
            WHERE fact.deployment_id = :deployment_id
              AND fact.fact_kind = eligible.fact_kind
              AND fact.fact_id = eligible.fact_id
            OFFSET 0
        ) presentation
    )
    """
