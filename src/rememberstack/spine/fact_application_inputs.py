"""Bounded adjudication inputs re-read under D118 claim and fact row locks."""

from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.core.context_references import MAX_CONTEXT_REFS
from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.fact_applications import canonical_entity
from rememberstack.spine.fact_applications import canonical_json
from rememberstack.spine.fact_applications import entity_members

# Bounds limit model input, never certify complete identity/evidence coverage.
_FACT_LIMIT = 20
_CONTEXT_FACT_LIMIT = 8
_CLAIM_LIMIT = 100
_ASSERTION_LIMIT = 100


def _rows(
    *, connection: Connection, sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Materialize only explicitly bounded payload queries."""
    return [dict(row) for row in connection.execute(text(sql), dict(params)).mappings()]


def _digest_rows(*, connection: Connection, sql: str, params: Mapping[str, Any]) -> str:
    """Stream complete membership/accounting into a hash without retaining payloads."""
    digest = sha256()
    result = connection.execution_options(stream_results=True).execute(
        text(sql), dict(params)
    )
    try:
        for row in result.mappings():
            digest.update(canonical_json(dict(row)).encode())
            digest.update(b"\n")
    finally:
        result.close()
        connection.execution_options(stream_results=False)
    return digest.hexdigest()


def application_snapshot(
    *,
    connection: Connection,
    deployment_id: UUID,
    root: UUID,
    members: list[UUID],
    application: Mapping[str, Any],
) -> dict[str, Any]:
    """Build coherent inputs after ordered locks, rejecting a changed participant set.

    The caller holds forget/identity/canonical entity locks. Candidate selection
    considers completed windows, and never uses dates as identity predicates.
    Exact triple or statement matches are nominated first, then full-text
    relevance fills the bounded set; both are nomination only.
    Model operations can address only the payload participants, not unseen members.
    """
    kind = application["output_kind"]
    if kind not in ("relation", "observation"):
        raise ValueError("unsupported fact plane")
    table, id_column = (
        ("relations", "relation_id")
        if kind == "relation"
        else ("observations", "observation_id")
    )
    evidence, pointer = f"{kind}_evidence", f"support_{kind}_id"
    params: dict[str, Any] = {
        "deployment_id": deployment_id,
        "members": members,
        "claim_id": application["claim_id"],
        "application_id": application["application_id"],
        "normalizer_version": application["normalizer_version"],
    }
    incoming = _rows(
        connection=connection,
        sql="""
      SELECT a.application_id,a.claim_id,a.subject_entity_id,a.object_entity_id,
             a.output_kind,a.output_ordinal,a.normalizer_version,a.adjudicator_version,
             a.support_relation_id,a.support_observation_id,a.support_stance,
             CASE WHEN a.output_kind='relation' THEN n.output->'relations'->a.output_ordinal
                  ELSE n.output->'observations'->a.output_ordinal END AS assertion
      FROM fact_applications a JOIN normalization_outputs n USING(deployment_id,claim_id,normalizer_version)
      WHERE a.application_id=:application_id AND a.deployment_id=:deployment_id
    """,
        params=params,
    )
    if len(incoming) != 1:
        raise ApplicationInputChanged("incoming application disappeared")
    assertion = incoming[0]["assertion"]
    params["query"] = assertion.get("statement") or " ".join(
        (
            assertion["subject"]["name"],
            assertion["predicate"],
            assertion["object"]["name"],
        )
    )
    content = (
        "f.statement"
        if kind == "observation"
        else "concat(s.canonical_name,' ',f.predicate,' ',o.canonical_name)"
    )
    joins = (
        ""
        if kind == "observation"
        else "JOIN entities s ON s.entity_id=f.subject_entity_id JOIN entities o ON o.entity_id=f.object_entity_id"
    )
    if kind == "relation":
        params["predicate"] = assertion["predicate"]
        params["object_members"] = entity_members(
            connection=connection,
            deployment_id=deployment_id,
            root=canonical_entity(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=UUID(str(incoming[0]["object_entity_id"])),
            ),
        )
        exact = "f.predicate=:predicate AND f.object_entity_id=ANY(:object_members)"
    else:
        params["statement"] = assertion["statement"]
        exact = "f.statement=:statement"
    selection = f"""WITH block AS (
          SELECT f.{id_column} AS fact_id,
                 CASE WHEN {exact} THEN 0 ELSE 1 END AS tier,
                 ts_rank_cd(to_tsvector('simple',{content}),plainto_tsquery('simple',:query)) AS rank
          FROM {table} f {joins}
          WHERE f.deployment_id=:deployment_id AND f.subject_entity_id=ANY(:members)
            AND f.invalidated_at IS NULL)
        SELECT fact_id, tier, rank FROM block ORDER BY tier, rank DESC, fact_id LIMIT {_FACT_LIMIT}"""
    context_members = _context_member_ids(
        connection=connection,
        deployment_id=deployment_id,
        root=root,
        application=incoming[0],
    )
    params["context_members"] = context_members
    extra_sql = f"""
        SELECT f.{id_column} AS fact_id,
               CASE WHEN {exact} THEN 0 ELSE 1 END AS tier,
               ts_rank_cd(to_tsvector('simple',{content}),plainto_tsquery('simple',:query)) AS rank
        FROM {table} f {joins}
        WHERE f.deployment_id=:deployment_id AND f.subject_entity_id=ANY(:members)
          AND f.invalidated_at IS NULL
          AND NOT f.{id_column} = ANY(CAST(:baseline_ids AS uuid[]))
          AND EXISTS (
            SELECT 1 FROM fact_applications a JOIN claims c
              ON c.deployment_id=a.deployment_id AND c.claim_id=a.claim_id
            WHERE a.deployment_id=:deployment_id
              AND a.{pointer}=f.{id_column}
              AND a.applied_at IS NOT NULL
              AND a.support_stance='supports'
              AND c.is_current_testimony
              AND (
                EXISTS (
                  SELECT 1 FROM application_context_bindings b
                  WHERE b.deployment_id=a.deployment_id
                    AND b.application_id=a.application_id
                    AND b.entity_id=ANY(CAST(:context_members AS uuid[]))
                    AND NOT b.entity_id=ANY(CAST(:members AS uuid[]))
                )
                OR (
                  a.object_entity_id IS NOT NULL
                  AND a.object_entity_id=ANY(CAST(:context_members AS uuid[]))
                  AND NOT a.object_entity_id=ANY(CAST(:members AS uuid[]))
                )
              )
          )
        ORDER BY tier, rank DESC, fact_id LIMIT {_CONTEXT_FACT_LIMIT}"""

    def _nominate_fact_ids() -> list[Any]:
        baseline = _rows(connection=connection, sql=selection, params=params)
        extra: list[dict[str, Any]] = []
        if context_members and baseline:
            extra = _rows(
                connection=connection,
                sql=extra_sql,
                params={**params, "baseline_ids": [row["fact_id"] for row in baseline]},
            )
        by_id = {row["fact_id"]: row for row in baseline}
        for row in extra:
            by_id.setdefault(row["fact_id"], row)
        shared_ids = {row["fact_id"] for row in extra}
        if context_members and baseline:
            shared_ids.update(
                row["fact_id"]
                for row in _rows(
                    connection=connection,
                    sql=f"""
        SELECT f.{id_column} AS fact_id FROM {table} f
        WHERE f.deployment_id=:deployment_id AND f.{id_column}=ANY(CAST(:shared_probe AS uuid[]))
          AND EXISTS (
            SELECT 1 FROM fact_applications a JOIN claims c
              ON c.deployment_id=a.deployment_id AND c.claim_id=a.claim_id
            WHERE a.deployment_id=:deployment_id
              AND a.{pointer}=f.{id_column}
              AND a.applied_at IS NOT NULL
              AND a.support_stance='supports'
              AND c.is_current_testimony
              AND (
                EXISTS (
                  SELECT 1 FROM application_context_bindings b
                  WHERE b.deployment_id=a.deployment_id
                    AND b.application_id=a.application_id
                    AND b.entity_id=ANY(CAST(:context_members AS uuid[]))
                    AND NOT b.entity_id=ANY(CAST(:members AS uuid[]))
                )
                OR (
                  a.object_entity_id IS NOT NULL
                  AND a.object_entity_id=ANY(CAST(:context_members AS uuid[]))
                  AND NOT a.object_entity_id=ANY(CAST(:members AS uuid[]))
                )
              )
          )""",
                    params={
                        **params,
                        "shared_probe": [row["fact_id"] for row in baseline],
                    },
                )
            )
        ordered = sorted(
            by_id.values(),
            key=lambda row: (
                0 if row["fact_id"] in shared_ids else 1,
                int(row["tier"]),
                -float(row["rank"] or 0),
                str(row["fact_id"]),
            ),
        )
        return [row["fact_id"] for row in ordered]

    params["fact_ids"] = _nominate_fact_ids()
    # Keep complete support membership in the fingerprint, but cap model copies.
    claim_selection = f"""SELECT c.claim_id FROM claims c
        WHERE c.deployment_id=:deployment_id AND (c.claim_id=:claim_id OR EXISTS (
          SELECT 1 FROM {evidence} e WHERE e.deployment_id=c.deployment_id
            AND e.claim_id=c.claim_id AND e.{id_column}=ANY(:fact_ids)))
        ORDER BY (c.claim_id=:claim_id) DESC,c.is_current_testimony DESC,c.asserted_at DESC NULLS LAST,c.claim_id
        LIMIT {_CLAIM_LIMIT}"""
    selected_claims = _rows(connection=connection, sql=claim_selection, params=params)
    params["claim_ids"] = sorted(row["claim_id"] for row in selected_claims)
    connection.execute(
        text(
            "SELECT claim_id FROM claims WHERE deployment_id=:deployment_id AND claim_id=ANY(:claim_ids) ORDER BY claim_id FOR UPDATE"
        ),
        params,
    ).all()
    connection.execute(
        text(
            f"SELECT {id_column} FROM {table} WHERE deployment_id=:deployment_id AND {id_column}=ANY(:fact_ids) ORDER BY {id_column} FOR UPDATE"
        ),
        params,
    ).all()
    # A lifecycle/review writer may have completed while these row locks waited.
    if set(_nominate_fact_ids()) != set(params["fact_ids"]) or {
        row["claim_id"]
        for row in _rows(connection=connection, sql=claim_selection, params=params)
    } != set(params["claim_ids"]):
        raise ApplicationInputChanged(
            "candidate/evidence nomination changed during lock wait"
        )
    facts = _rows(
        connection=connection,
        sql=f"""
      SELECT f.{id_column} AS fact_id,f.subject_entity_id,{content} AS statement,
             f.valid_from,f.valid_until,f.valid_precision,f.window_claim_ids,
             f.ingested_at,f.invalidated_at,f.contradiction_group,f.evidence_count,f.contradict_count
             {",f.predicate,f.object_entity_id" if kind == "relation" else ""}
      FROM {table} f {joins} WHERE f.deployment_id=:deployment_id AND f.{id_column}=ANY(:fact_ids)
      ORDER BY f.{id_column}
    """,
        params=params,
    )
    order_index = {fact_id: index for index, fact_id in enumerate(params["fact_ids"])}
    facts.sort(key=lambda fact: order_index[fact["fact_id"]])
    for fact in facts:
        fact["window_claim_ids"] = sorted(fact["window_claim_ids"])
    claims = _rows(
        connection=connection,
        sql="""
      SELECT claim_id,doc_id,claim_text,source_span,asserted_at,claim_valid_from,claim_valid_until,
             claim_valid_precision,claim_valid_kind,is_current_testimony,is_attributed,extractor_version
      FROM claims WHERE deployment_id=:deployment_id AND claim_id=ANY(:claim_ids) ORDER BY claim_id
    """,
        params=params,
    )
    if application["claim_id"] not in {claim["claim_id"] for claim in claims}:
        raise ApplicationInputChanged("incoming source disappeared")
    supports = _rows(
        connection=connection,
        sql=f"""
      SELECT e.{id_column} AS fact_id,e.claim_id,e.stance
      FROM {evidence} e WHERE e.deployment_id=:deployment_id AND e.{id_column}=ANY(:fact_ids)
        AND e.claim_id=ANY(:claim_ids) ORDER BY e.{id_column},e.claim_id
    """,
        params=params,
    )
    assertions = _rows(
        connection=connection,
        sql=f"""
      SELECT a.application_id,a.claim_id,a.subject_entity_id,a.object_entity_id,a.output_kind,
             a.output_ordinal,a.normalizer_version,a.adjudicator_version,
             a.support_relation_id,a.support_observation_id,a.support_stance,
             CASE WHEN a.output_kind='relation' THEN n.output->'relations'->a.output_ordinal
                  ELSE n.output->'observations'->a.output_ordinal END AS assertion
      FROM fact_applications a JOIN normalization_outputs n USING(deployment_id,claim_id,normalizer_version)
      WHERE a.deployment_id=:deployment_id AND a.{pointer}=ANY(:fact_ids) AND a.claim_id=ANY(:claim_ids)
        AND a.subject_entity_id=ANY(:members) AND a.application_id<>:application_id
      ORDER BY a.application_id LIMIT {_ASSERTION_LIMIT}
    """,
        params=params,
    )
    # Fact locks serialize every support pointer mutation before application locks.
    params["application_ids"] = sorted(
        [application["application_id"], *(row["application_id"] for row in assertions)]
    )
    connection.execute(
        text(
            "SELECT application_id FROM fact_applications WHERE deployment_id=:deployment_id AND application_id=ANY(:application_ids) ORDER BY application_id FOR UPDATE"
        ),
        params,
    ).all()
    member_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT {id_column} FROM {table} WHERE deployment_id=:deployment_id AND subject_entity_id=ANY(:members)
        AND invalidated_at IS NULL ORDER BY {id_column}
    """,
        params=params,
    )
    evidence_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT {id_column},claim_id,stance FROM {evidence}
      WHERE deployment_id=:deployment_id AND {id_column}=ANY(:fact_ids) ORDER BY {id_column},claim_id
    """,
        params=params,
    )
    support_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT application_id,claim_id,{pointer},support_stance FROM fact_applications
      WHERE deployment_id=:deployment_id AND {pointer}=ANY(:fact_ids) ORDER BY application_id
    """,
        params=params,
    )
    binding_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT a.application_id,a.support_stance,c.is_current_testimony,c.claim_id,
             b.ordinal,b.entity_id,b.resolver_decision_id
      FROM fact_applications a
      JOIN claims c ON c.deployment_id=a.deployment_id AND c.claim_id=a.claim_id
      LEFT JOIN application_context_bindings b
        ON b.deployment_id=a.deployment_id AND b.application_id=a.application_id
      WHERE a.deployment_id=:deployment_id
        AND (
          a.application_id=:application_id
          OR a.{pointer}=ANY(CAST(:fact_ids AS uuid[]))
          OR (
            a.subject_entity_id=ANY(CAST(:members AS uuid[]))
            AND a.applied_at IS NOT NULL
            AND a.support_stance='supports'
            AND c.is_current_testimony
            AND (
              b.entity_id=ANY(CAST(:context_members AS uuid[]))
              OR (
                a.object_entity_id IS NOT NULL
                AND a.object_entity_id=ANY(CAST(:context_members AS uuid[]))
              )
            )
          )
        )
      ORDER BY a.application_id, b.ordinal NULLS FIRST
    """,
        params=params,
    )
    canonical_membership_hash = _digest_rows(
        connection=connection,
        sql="""
      SELECT member_id FROM unnest(CAST(:context_members AS uuid[])) AS members(member_id)
      ORDER BY member_id
    """,
        params=params,
    )
    context_hash = sha256(
        f"{binding_hash}\n{canonical_membership_hash}".encode()
    ).hexdigest()
    all_assertions = sorted(
        [*incoming, *assertions], key=lambda row: row["application_id"]
    )
    context_rows = _rows(
        connection=connection,
        sql="""
        WITH RECURSIVE bindings AS (
          SELECT b.application_id,b.ordinal,e.entity_id,e.canonical_name
          FROM application_context_bindings b JOIN entities e
            ON e.deployment_id=b.deployment_id AND e.entity_id=b.entity_id
          WHERE b.deployment_id=:deployment_id
            AND b.application_id=ANY(CAST(:application_ids AS uuid[]))
        ), chain AS (
          SELECT b.entity_id AS origin,e.entity_id,e.merged_into,e.canonical_name,
                 ARRAY[e.entity_id] AS path
          FROM (SELECT DISTINCT entity_id FROM bindings) b JOIN entities e
            ON e.entity_id=b.entity_id AND e.deployment_id=:deployment_id
          UNION ALL
          SELECT c.origin,e.entity_id,e.merged_into,e.canonical_name,c.path || e.entity_id
          FROM chain c JOIN entities e ON e.entity_id=c.merged_into
          WHERE e.deployment_id=:deployment_id AND NOT e.entity_id=ANY(c.path)
        )
        SELECT b.application_id,b.ordinal,b.entity_id,b.canonical_name AS name,
               c.entity_id AS canonical_entity_id,c.canonical_name
        FROM bindings b LEFT JOIN chain c ON c.origin=b.entity_id AND c.merged_into IS NULL
        ORDER BY b.application_id,b.ordinal
        """,
        params={
            "deployment_id": deployment_id,
            "application_ids": [row["application_id"] for row in all_assertions],
        },
    )
    contexts_by_application: dict[UUID, list[dict[str, Any]]] = {}
    for context in context_rows:
        if context["canonical_entity_id"] is None:
            raise ApplicationInputChanged(
                "context entity has no surviving canonical root"
            )
        contexts_by_application.setdefault(context["application_id"], []).append(
            {
                key: context[key]
                for key in (
                    "entity_id",
                    "name",
                    "canonical_entity_id",
                    "canonical_name",
                )
            }
        )
    for row in all_assertions:
        row["context_entities"] = contexts_by_application.get(row["application_id"], [])
        row["canonical_subject_id"] = canonical_entity(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=row["subject_entity_id"],
        )
        row["canonical_object_id"] = (
            canonical_entity(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=row["object_entity_id"],
            )
            if row["object_entity_id"]
            else None
        )
    return {
        "deployment_id": deployment_id,
        "root": root,
        "kind": kind,
        "application_id": application["application_id"],
        "normalizer_version": application["normalizer_version"],
        "adjudicator_version": application["adjudicator_version"],
        "assertions": all_assertions,
        "facts": facts,
        "claims": claims,
        "evidence": supports,
        "membership_hash": member_hash,
        "evidence_hash": evidence_hash,
        "support_hash": support_hash,
        "context_hash": context_hash,
        "context_member_ids": [str(entity_id) for entity_id in context_members],
        "context_truncated": len(assertion.get("context_refs") or [])
        > MAX_CONTEXT_REFS,
        "limits": {
            "facts": _FACT_LIMIT,
            "context_facts": _CONTEXT_FACT_LIMIT,
            "claims": _CLAIM_LIMIT,
            "assertions": _ASSERTION_LIMIT,
        },
        "potentially_truncated": len(facts) >= _FACT_LIMIT
        or len(claims) == _CLAIM_LIMIT
        or len(assertions) == _ASSERTION_LIMIT,
    }


def _context_member_ids(
    *,
    connection: Connection,
    deployment_id: UUID,
    root: UUID,
    application: Mapping[str, Any],
) -> list[UUID]:
    """Canonical reverse-closure of incoming context, excluding the subject."""
    rows = _rows(
        connection=connection,
        sql="""
      SELECT entity_id FROM application_context_bindings
      WHERE deployment_id=:deployment_id AND application_id=:application_id
      ORDER BY ordinal
    """,
        params={
            "deployment_id": deployment_id,
            "application_id": application["application_id"],
        },
    )
    entity_ids = [UUID(str(row["entity_id"])) for row in rows]
    object_id = application.get("object_entity_id")
    if object_id is not None:
        entity_ids.append(UUID(str(object_id)))
    members: list[UUID] = []
    seen: set[UUID] = set()
    for entity_id in entity_ids:
        canonical = canonical_entity(
            connection=connection, deployment_id=deployment_id, entity_id=entity_id
        )
        if canonical == root:
            continue
        for member in entity_members(
            connection=connection, deployment_id=deployment_id, root=canonical
        ):
            if member not in seen:
                seen.add(member)
                members.append(member)
    return members
